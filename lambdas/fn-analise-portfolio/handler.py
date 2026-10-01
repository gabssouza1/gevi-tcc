"""Função Lambda ``fn-analise-portfolio``.

Aciona: Agente_Selecao_Ativos (via AgentCore Gateway / tool routing).
Responsabilidade: analisar a composição e a diversificação do Portfólio do
usuário (tabela DynamoDB ``Portfolios`` + datasets em ``s3-investimentos``) e
**identificar/reportar concentrações acima de 30%** (Propriedade 8 do design).

Requisitos cobertos:
    - 9.1  Analisar a composição e a diversificação do Portfólio por
           ``Tipos_Ativo`` (RENDA_FIXA, RENDA_VARIAVEL, FII, CRIPTO).
    - 9.2  Identificar e reportar concentrações acima do limiar de 30%
           (``LIMIAR_CONCENTRACAO_PCT``) — reutiliza ``identificar_concentracoes``.
    - 9.4  Sugerir ajustes no Portfólio quando houver desalinhamento com o
           Perfil_Investidor (tolerância a risco) ou concentração excessiva.

A lógica pura (montagem do portfólio, cálculo de diversificação, concentrações
e sugestões) é mantida separada da camada de I/O (DynamoDB + S3), como nas
demais Lambdas, para facilitar testes unitários e o property test (Tarefa 6.5).
Apenas operações de leitura são realizadas.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key

from shared import (
    LIMIAR_CONCENTRACAO_PCT,
    AtivoPortfolio,
    Nivel_Risco,
    Perfil_Risco,
    Portfolio,
    Tipos_Ativo,
    identificar_concentracoes,
    log_evento,
    obter_logger,
    to_dict,
)

logger = obter_logger(__name__)

# Nomes dos recursos lidos de variáveis de ambiente (definidas pelo CDK). Os
# valores padrão espelham o design para facilitar execução local/testes.
_ENV_TABELA_PORTFOLIOS = "TABELA_PORTFOLIOS"
_TABELA_PADRAO = "Portfolios"

_ENV_BUCKET_INVESTIMENTOS = "BUCKET_INVESTIMENTOS"
_BUCKET_PADRAO = "s3-investimentos"

# Prefixo dos datasets (séries históricas, CSVs de cotações) no bucket.
_PREFIXO_DATASETS = "datasets/"

# Atributos de chave da tabela ``Portfolios`` (ver design/conftest).
_ATRIBUTO_PK = "userId"
_ATRIBUTO_SK = "assetId"

# Mapeamento de cada classe de ativo ao nível de risco associado. Usado para
# avaliar o alinhamento do Portfólio com a tolerância a risco do perfil (Req 9.4).
_RISCO_POR_TIPO: dict[Tipos_Ativo, Nivel_Risco] = {
    Tipos_Ativo.RENDA_FIXA: Nivel_Risco.BAIXO,
    Tipos_Ativo.FII: Nivel_Risco.MEDIO,
    Tipos_Ativo.RENDA_VARIAVEL: Nivel_Risco.ALTO,
    Tipos_Ativo.CRIPTO: Nivel_Risco.ALTO,
}

# Percentual máximo aceitável em ativos de risco ALTO por perfil de tolerância
# a risco. Acima disso, o Portfólio é considerado desalinhado (Req 9.4).
_LIMITE_ALTO_RISCO_POR_PERFIL: dict[Perfil_Risco, float] = {
    Perfil_Risco.CONSERVADOR: 20.0,
    Perfil_Risco.MODERADO: 50.0,
    Perfil_Risco.ARROJADO: 100.0,
}


# ---------------------------------------------------------------------------
# Lógica pura (sem I/O) — facilmente testável
# ---------------------------------------------------------------------------
def _para_float(valor: Any) -> float:
    """Converte números do DynamoDB (``Decimal``) para ``float`` de forma segura."""
    if valor is None:
        return 0.0
    if isinstance(valor, Decimal):
        return float(valor)
    if isinstance(valor, bool):  # evita tratar bool como número
        return 0.0
    if isinstance(valor, (int, float)):
        return float(valor)
    return 0.0


def item_para_ativo(item: dict[str, Any]) -> AtivoPortfolio:
    """Converte um item do DynamoDB em um :class:`AtivoPortfolio` (leitura).

    Aceita tanto os nomes de chave físicos (``userId``/``assetId``) quanto os do
    modelo (``user_id``/``asset_id``) e normaliza os campos numéricos.
    """
    user_id = item.get(_ATRIBUTO_PK) or item.get("user_id") or ""
    asset_id = item.get(_ATRIBUTO_SK) or item.get("asset_id") or ""
    return AtivoPortfolio(
        user_id=str(user_id),
        asset_id=str(asset_id),
        tipo_ativo=Tipos_Ativo(item.get("tipo_ativo")),
        percentual=_para_float(item.get("percentual")),
        valor=_para_float(item.get("valor")),
    )


def montar_portfolio(user_id: str, itens: list[dict[str, Any]]) -> Portfolio:
    """Monta o :class:`Portfolio` do usuário a partir dos itens do DynamoDB."""
    ativos = [item_para_ativo(item) for item in itens]
    return Portfolio(user_id=user_id, ativos=ativos)


def analisar_diversificacao(portfolio: Portfolio) -> dict[str, Any]:
    """Analisa a composição/diversificação do Portfólio por ``Tipos_Ativo`` (Req 9.1).

    Agrega o percentual e o valor de cada classe de ativo presente e informa
    quantas classes distintas o Portfólio possui (medida simples de
    diversificação).
    """
    por_tipo: dict[str, dict[str, Any]] = {}
    for ativo in portfolio.ativos:
        chave = ativo.tipo_ativo.value
        agregado = por_tipo.setdefault(
            chave, {"percentual": 0.0, "valor": 0.0, "quantidade": 0}
        )
        agregado["percentual"] += ativo.percentual
        agregado["valor"] += ativo.valor
        agregado["quantidade"] += 1

    return {
        "por_tipo": por_tipo,
        "tipos_presentes": sorted(por_tipo.keys()),
        "tipos_distintos": len(por_tipo),
    }


def analisar_concentracoes(portfolio: Portfolio) -> list[dict[str, Any]]:
    """Identifica e reporta as concentrações acima de 30% (Req 9.2, Propriedade 8).

    Reutiliza ``identificar_concentracoes`` de ``shared`` para garantir que o
    limiar aplicado é exatamente ``LIMIAR_CONCENTRACAO_PCT``.
    """
    concentrados = identificar_concentracoes(portfolio)
    return [
        {
            "asset_id": ativo.asset_id,
            "tipo_ativo": ativo.tipo_ativo.value,
            "percentual": ativo.percentual,
            "valor": ativo.valor,
        }
        for ativo in concentrados
    ]


def percentual_por_nivel_risco(portfolio: Portfolio) -> dict[str, float]:
    """Soma o percentual do Portfólio agrupado pelo nível de risco de cada tipo."""
    acumulado: dict[str, float] = {nivel.value: 0.0 for nivel in Nivel_Risco}
    for ativo in portfolio.ativos:
        nivel = _RISCO_POR_TIPO.get(ativo.tipo_ativo)
        if nivel is not None:
            acumulado[nivel.value] += ativo.percentual
    return acumulado


def sugerir_ajustes(
    portfolio: Portfolio,
    concentracoes: list[dict[str, Any]],
    tolerancia: Perfil_Risco | None,
) -> list[str]:
    """Sugere ajustes no Portfólio (Req 9.4).

    Gera sugestões quando:
    - há concentração acima de 30% em um único ativo (risco de diversificação);
    - o Portfólio possui uma única classe de ativo (baixa diversificação);
    - a exposição a ativos de risco ALTO excede o limite adequado ao perfil de
      tolerância a risco informado (desalinhamento perfil-portfólio).
    """
    sugestoes: list[str] = []

    # Concentração excessiva em ativos individuais (Req 9.2 → ação de ajuste).
    for concentracao in concentracoes:
        sugestoes.append(
            f"Reduzir a exposição ao ativo '{concentracao['asset_id']}' "
            f"({concentracao['percentual']:.1f}% do portfólio), que excede o "
            f"limiar de concentração de {LIMIAR_CONCENTRACAO_PCT:.0f}%."
        )

    # Baixa diversificação: portfólio concentrado em uma única classe de ativo.
    diversificacao = analisar_diversificacao(portfolio)
    if portfolio.ativos and diversificacao["tipos_distintos"] == 1:
        (unico_tipo,) = diversificacao["tipos_presentes"]
        sugestoes.append(
            f"Diversificar o portfólio: todos os ativos são da classe "
            f"'{unico_tipo}'. Considere distribuir entre outras classes de ativo."
        )

    # Desalinhamento com o perfil de tolerância a risco (Req 9.4).
    if tolerancia is not None and portfolio.ativos:
        limite = _LIMITE_ALTO_RISCO_POR_PERFIL[tolerancia]
        exposicao_alta = percentual_por_nivel_risco(portfolio)[Nivel_Risco.ALTO.value]
        if exposicao_alta > limite:
            sugestoes.append(
                f"A exposição a ativos de risco alto ({exposicao_alta:.1f}%) "
                f"está acima do adequado para o perfil {tolerancia.value} "
                f"(limite de {limite:.0f}%). Considere realocar parte para "
                f"ativos de menor risco."
            )

    return sugestoes


def analisar_portfolio(
    portfolio: Portfolio,
    *,
    tolerancia: Perfil_Risco | None = None,
    datasets: list[str] | None = None,
    agora: datetime | None = None,
) -> dict[str, Any]:
    """Monta a resposta consolidada da análise de Portfólio (lógica pura).

    Combina diversificação (Req 9.1), concentrações acima de 30% (Req 9.2 /
    Propriedade 8) e sugestões de ajuste (Req 9.4). ``datasets`` lista os ativos
    disponíveis lidos do S3 (compõe o output do fluxo do Agente_Selecao_Ativos).
    """
    agora = agora or datetime.now(timezone.utc)
    concentracoes = analisar_concentracoes(portfolio)
    valor_total = sum(ativo.valor for ativo in portfolio.ativos)

    return {
        "user_id": portfolio.user_id,
        "possui_portfolio": bool(portfolio.ativos),
        "total_ativos": len(portfolio.ativos),
        "valor_total": valor_total,
        "ativos": [to_dict(ativo) for ativo in portfolio.ativos],
        "diversificacao": analisar_diversificacao(portfolio),
        "exposicao_por_nivel_risco": percentual_por_nivel_risco(portfolio),
        "limiar_concentracao_pct": LIMIAR_CONCENTRACAO_PCT,
        "concentracoes": concentracoes,
        "possui_concentracao_excessiva": bool(concentracoes),
        "sugestoes": sugerir_ajustes(portfolio, concentracoes, tolerancia),
        "datasets_disponiveis": datasets or [],
        "gerado_em": agora.isoformat(),
    }


def _parse_tolerancia(valor: Any) -> Perfil_Risco | None:
    """Interpreta a tolerância a risco do ``event`` (ausente/ inválida → ``None``)."""
    if valor is None:
        return None
    if isinstance(valor, Perfil_Risco):
        return valor
    try:
        return Perfil_Risco(valor)
    except ValueError:
        return None


def _extrair_user_id(event: dict[str, Any]) -> str | None:
    """Extrai o ``user_id`` do ``event`` (aceita ``user_id``/``userId``)."""
    envelope = event.get("portfolio") or event.get("dados") or {}
    if not isinstance(envelope, dict):
        envelope = {}
    user_id = (
        event.get("user_id")
        or event.get("userId")
        or envelope.get("user_id")
        or envelope.get("userId")
    )
    if isinstance(user_id, str) and user_id.strip():
        return user_id
    return None


# ---------------------------------------------------------------------------
# Acesso ao DynamoDB e S3 (I/O) — somente leitura
# ---------------------------------------------------------------------------
def _obter_tabela():
    """Devolve o recurso de tabela ``Portfolios`` (nome via env var)."""
    nome_tabela = os.environ.get(_ENV_TABELA_PORTFOLIOS, _TABELA_PADRAO)
    dynamodb = boto3.resource("dynamodb")
    return dynamodb.Table(nome_tabela)


def consultar_ativos(tabela, user_id: str) -> list[dict[str, Any]]:
    """Lê todos os ativos do Portfólio de um usuário (query + paginação)."""
    itens: list[dict[str, Any]] = []
    argumentos: dict[str, Any] = {
        "KeyConditionExpression": Key(_ATRIBUTO_PK).eq(user_id)
    }
    while True:
        resposta = tabela.query(**argumentos)
        itens.extend(resposta.get("Items", []))
        chave_exclusiva = resposta.get("LastEvaluatedKey")
        if not chave_exclusiva:
            break
        argumentos["ExclusiveStartKey"] = chave_exclusiva
    return itens


def _obter_cliente_s3():
    """Devolve o cliente boto3 do S3 para leitura dos datasets."""
    return boto3.client("s3")


def listar_datasets(
    cliente_s3, bucket: str, prefixo: str = _PREFIXO_DATASETS
) -> list[str]:
    """Lista os datasets disponíveis no S3 (ativos/ séries) — somente leitura.

    Retorna os nomes dos objetos sob ``datasets/`` (sem o próprio "diretório").
    Falhas de acesso ao S3 não interrompem a análise do Portfólio: retorna lista
    vazia para manter a função resiliente.
    """
    datasets: list[str] = []
    try:
        paginador = cliente_s3.get_paginator("list_objects_v2")
        for pagina in paginador.paginate(Bucket=bucket, Prefix=prefixo):
            for objeto in pagina.get("Contents", []):
                chave = objeto.get("Key", "")
                # Ignora o marcador do próprio prefixo (objeto "diretório").
                if chave and chave != prefixo:
                    datasets.append(chave[len(prefixo):] if chave.startswith(prefixo) else chave)
    except Exception:  # noqa: BLE001 — S3 é enriquecimento opcional da análise
        return []
    return datasets


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Ponto de entrada da Lambda acionada pelo Agente_Selecao_Ativos.

    ``event``:
        - ``user_id``/``userId`` (obrigatório): usuário cujo Portfólio será analisado.
        - ``tolerancia_risco``/``toleranciaRisco`` (opcional): perfil de risco do
          usuário, usado para avaliar o alinhamento do Portfólio (Req 9.4).

    Retorna a composição/diversificação do Portfólio, as concentrações acima de
    30% (Req 9.2) e as sugestões de ajuste (Req 9.4). Não realiza escritas.
    """
    event = event or {}
    if not isinstance(event, dict):
        return {"sucesso": False, "erro": "O event deve ser um objeto JSON."}

    log_evento(logger, "invocacao_recebida", context=context)
    user_id = _extrair_user_id(event)
    if user_id is None:
        return {
            "sucesso": False,
            "erro": "O campo 'user_id' é obrigatório para analisar o Portfólio.",
        }

    tolerancia = _parse_tolerancia(
        event.get("tolerancia_risco") or event.get("toleranciaRisco")
    )

    tabela = _obter_tabela()
    itens = consultar_ativos(tabela, user_id)
    portfolio = montar_portfolio(user_id, itens)

    bucket = os.environ.get(_ENV_BUCKET_INVESTIMENTOS, _BUCKET_PADRAO)
    datasets = listar_datasets(_obter_cliente_s3(), bucket)

    analise = analisar_portfolio(portfolio, tolerancia=tolerancia, datasets=datasets)
    log_evento(
        logger,
        "invocacao_concluida",
        context=context,
        concentracoes=len(analise.get("concentracoes", [])),
    )
    return {"sucesso": True, **analise}
