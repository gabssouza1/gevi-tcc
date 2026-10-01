"""Função Lambda ``fn-consulta-indicadores``.

Aciona: Agente_Macroeconomico (e o Dashboard econômico).
Responsabilidade: **ler** os indicadores mais recentes de ``EconomicIndicators``
e calcular as variações percentuais em relação ao período anterior. Apenas
operações de leitura — nunca escreve nada (Propriedade 10 do design).

Requisitos atendidos:
    - 7.1  Disponibilizar os valores atuais de Selic, IPCA, dólar e CDI.
    - 7.2  Fornecer os dados mais recentes disponíveis dos indicadores.
    - 7.3  Apresentar a variação percentual em relação ao período anterior.
    - 7.4  Quando um indicador estiver indisponível/desatualizado, retornar a
           última informação disponível com a respectiva data de atualização.

A lógica de cálculo (funções puras) é mantida separada do acesso ao DynamoDB
(I/O) para facilitar os testes unitários e o property test (Tarefa 5.8).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key

from shared import Indicador, log_evento, obter_logger, to_dict

logger = obter_logger(__name__)

# Nome da tabela lido de variável de ambiente (definida pelo CDK). O valor
# padrão espelha o nome do design para facilitar execução local/testes.
_ENV_TABELA = "TABELA_INDICADORES"
_TABELA_PADRAO = "EconomicIndicators"

# Atributo de partition key da tabela ``EconomicIndicators`` (ver design/conftest).
_ATRIBUTO_PK = "indicatorId"

# Indicadores macroeconômicos padrão do Dashboard (Requisito 7.1).
INDICADORES_PADRAO: tuple[str, ...] = ("SELIC", "IPCA", "DOLAR", "CDI")


# ---------------------------------------------------------------------------
# Lógica pura (sem I/O) — facilmente testável
# ---------------------------------------------------------------------------
def calcular_variacao_percentual(
    valor_atual: float, valor_anterior: float | None
) -> float | None:
    """Calcula a variação percentual entre o valor atual e o anterior (Req 7.3).

    Retorna ``None`` quando não há período anterior ou quando o valor anterior é
    zero (evita divisão por zero). Caso contrário, devolve
    ``((atual - anterior) / |anterior|) * 100``.
    """
    if valor_anterior is None or valor_anterior == 0:
        return None
    return ((valor_atual - valor_anterior) / abs(valor_anterior)) * 100.0


def _para_float(valor: Any) -> float | None:
    """Converte números do DynamoDB (``Decimal``) para ``float`` de forma segura."""
    if valor is None:
        return None
    if isinstance(valor, Decimal):
        return float(valor)
    if isinstance(valor, bool):  # evita tratar bool como número
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    return None


def item_para_indicador(item: dict[str, Any]) -> Indicador:
    """Converte um item do DynamoDB em um :class:`Indicador` (apenas leitura).

    Aceita tanto o nome de chave físico (``indicatorId``) quanto o do modelo
    (``indicator_id``), e normaliza os campos numéricos para ``float``.
    """
    indicator_id = item.get(_ATRIBUTO_PK) or item.get("indicator_id") or ""
    return Indicador(
        indicator_id=str(indicator_id),
        date=str(item.get("date", "")),
        valor=_para_float(item.get("valor")) or 0.0,
        valor_anterior=_para_float(item.get("valor_anterior")),
        variacao_percentual=_para_float(item.get("variacao_percentual")),
        unidade=str(item.get("unidade", "")),
        valor_anualizado=_para_float(item.get("valor_anualizado")),
    )


def _parse_data(data_iso: str) -> datetime | None:
    """Interpreta uma data ISO-8601 (data ou datetime) como ``datetime`` UTC."""
    if not data_iso:
        return None
    texto = data_iso.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(texto)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def esta_desatualizado(
    data_iso: str, agora: datetime, max_idade_horas: float | None
) -> bool:
    """Indica se a última atualização excede o limite de idade informado (Req 7.4).

    Sem limite (``max_idade_horas is None``) o dado nunca é considerado
    desatualizado. Datas não interpretáveis são tratadas como desatualizadas.
    """
    if max_idade_horas is None:
        return False
    dt = _parse_data(data_iso)
    if dt is None:
        return True
    idade_horas = (agora - dt).total_seconds() / 3600.0
    return idade_horas > max_idade_horas


def montar_indicador(
    recentes: list[Indicador],
    *,
    agora: datetime,
    max_idade_horas: float | None = None,
) -> dict[str, Any]:
    """Monta a resposta de um indicador a partir dos itens mais recentes.

    ``recentes`` deve estar ordenado do mais recente para o mais antigo. O valor
    anterior usado no cálculo da variação vem do segundo item mais recente; se
    houver apenas um item, recorre ao ``valor_anterior`` já persistido nele.
    Sempre inclui ``data_atualizacao`` para satisfazer o Requisito 7.4.
    """
    if not recentes:
        return {"disponivel": False}

    atual = recentes[0]
    # "Período anterior" (Requisito 7.3) é o valor do mês/dia anterior da
    # série real (BCB), já calculado e persistido em ``valor_anterior`` no
    # próprio item por fn-consulta-APIs (parsear_serie_bcb usa a penúltima
    # observação da série oficial). NÃO usar o segundo item mais recente
    # desta tabela (``recentes[1]``): como o pipeline grava um novo item a
    # cada 5 minutos (rate(5 minutes)) mesmo sem o valor mudar, o "anterior
    # da tabela" quase sempre é uma leitura de minutos atrás com o mesmo
    # valor — o que fazia a variação aparecer sempre como 0,00% mesmo
    # quando a taxa realmente mudou há dias/semanas.
    valor_anterior: float | None = atual.valor_anterior

    variacao = calcular_variacao_percentual(atual.valor, valor_anterior)

    indicador = Indicador(
        indicator_id=atual.indicator_id,
        date=atual.date,
        valor=atual.valor,
        valor_anterior=valor_anterior,
        variacao_percentual=variacao,
        unidade=atual.unidade,
        valor_anualizado=atual.valor_anualizado,
    )

    resultado = to_dict(indicador)
    resultado["disponivel"] = True
    resultado["data_atualizacao"] = atual.date
    resultado["desatualizado"] = esta_desatualizado(atual.date, agora, max_idade_horas)
    return resultado


# ---------------------------------------------------------------------------
# Acesso ao DynamoDB (I/O) — somente leitura
# ---------------------------------------------------------------------------
def _obter_tabela():
    """Devolve o recurso de tabela ``EconomicIndicators`` (nome via env var)."""
    nome_tabela = os.environ.get(_ENV_TABELA, _TABELA_PADRAO)
    dynamodb = boto3.resource("dynamodb")
    return dynamodb.Table(nome_tabela)


def consultar_recentes(tabela, indicator_id: str, limite: int = 2) -> list[Indicador]:
    """Lê os ``limite`` itens mais recentes de um indicador (ordem decrescente).

    A ordenação decrescente pela sort key ``date`` (ISO-8601) garante que o
    primeiro item seja o mais recente disponível (Requisito 7.2).
    """
    resposta = tabela.query(
        KeyConditionExpression=Key(_ATRIBUTO_PK).eq(indicator_id),
        ScanIndexForward=False,
        Limit=limite,
    )
    return [item_para_indicador(item) for item in resposta.get("Items", [])]


def consultar_indicadores(
    tabela,
    indicator_ids: list[str],
    *,
    agora: datetime | None = None,
    max_idade_horas: float | None = None,
) -> list[dict[str, Any]]:
    """Consulta e monta a resposta de cada indicador solicitado (somente leitura)."""
    agora = agora or datetime.now(timezone.utc)
    resultados: list[dict[str, Any]] = []
    for indicator_id in indicator_ids:
        recentes = consultar_recentes(tabela, indicator_id)
        indicador = montar_indicador(
            recentes, agora=agora, max_idade_horas=max_idade_horas
        )
        indicador.setdefault("indicator_id", indicator_id)
        resultados.append(indicador)
    return resultados


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Ponto de entrada da Lambda.

    ``event`` (todos opcionais):
        - ``indicadores``: lista de ``indicator_id`` a consultar
          (padrão: Selic, IPCA, dólar, CDI).
        - ``max_idade_horas``: limite de idade para marcar um indicador como
          desatualizado (Requisito 7.4). Ausente → nunca desatualizado.

    Retorna os indicadores mais recentes com suas variações percentuais para o
    Dashboard/Agente_Macroeconomico. Não realiza nenhuma escrita.
    """
    event = event or {}
    log_evento(logger, "invocacao_recebida", context=context)
    indicator_ids = event.get("indicadores") or list(INDICADORES_PADRAO)
    max_idade_horas = event.get("max_idade_horas")

    agora = datetime.now(timezone.utc)
    tabela = _obter_tabela()
    indicadores = consultar_indicadores(
        tabela, indicator_ids, agora=agora, max_idade_horas=max_idade_horas
    )

    log_evento(
        logger, "invocacao_concluida", context=context, indicadores=len(indicadores)
    )
    return {
        "indicadores": indicadores,
        "gerado_em": agora.isoformat(),
    }
