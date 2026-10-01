"""Função Lambda ``fn-calculo-simulacao``.

Aciona: Agente_Risco.

Responsabilidades (Tarefa 6.1):
  - Calcular a **volatilidade** e a **exposição financeira** de uma simulação
    (Requisito 4.1) e classificar o **nível de risco** em categorias
    compreensíveis — BAIXO / MEDIO / ALTO (Requisito 4.2).
  - Projetar três **cenários** de rendimento respeitando a ordem
    pessimista ≤ realista ≤ otimista (Requisitos 6.1, 6.2 / Propriedade 4).
  - **Ler** os indicadores macroeconômicos de ``EconomicIndicators`` para
    parametrizar os cálculos (apenas leitura).
  - Anexar o **disclaimer** obrigatório de projeções (Requisito 6.3).
  - Quando os dados de mercado estiverem indisponíveis, informar que a análise
    de risco está **temporariamente indisponível** (Requisito 4.4).

O módulo separa a lógica pura de cálculo (sem rede nem AWS) do acesso ao
DynamoDB (I/O), facilitando os testes unitários e os property tests das
Tarefas 6.2 (presença de análise de risco) e 6.3 (consistência de cenários).

A persistência da simulação no ``Historico`` (Tarefa 6.8 / Requisito 2.6) é
feita ao final do cálculo: a ``Simulacao`` montada e validada, junto da
``AnaliseRisco``, é gravada de forma consultável (PK ``userId``, SK
``timestamp``). A escrita é resiliente — uma falha ao persistir é registrada em
log e não derruba a resposta da simulação — e só ocorre para usuários reais
(``user_id`` diferente de ``desconhecido``); simulações anônimas não são gravadas.
"""

from __future__ import annotations

import json
import logging
import math
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key

from shared import (
    AnaliseRisco,
    ErroValidacao,
    Nivel_Risco,
    Simulacao,
    Tipos_Ativo,
    log_evento,
    to_dict,
    validar_simulacao,
)

logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)

# ---------------------------------------------------------------------------
# Configuração e parâmetros do modelo de simulação
# ---------------------------------------------------------------------------
# Nome da tabela lido de variável de ambiente (definida pelo CDK). O padrão
# espelha o nome do design para facilitar execução local/testes.
_ENV_TABELA = "TABELA_INDICADORES"
_TABELA_PADRAO = "EconomicIndicators"

# Atributo de partition key da tabela ``EconomicIndicators`` (ver design/conftest).
_ATRIBUTO_PK = "indicatorId"

# Tabela ``Historico`` (PK ``userId`` + SK ``timestamp``) onde a simulação é
# persistida de forma consultável (Requisito 2.6). Nome via env var (CDK).
_ENV_TABELA_HISTORICO = "TABELA_HISTORICO"
_TABELA_HISTORICO_PADRAO = "Historico"

# Marcador do tipo de registro no ``Historico``, permitindo distinguir simulações
# de recomendações (que também são persistidas nessa tabela) ao consultar.
TIPO_REGISTRO_SIMULACAO = "SIMULACAO"

# Valor sentinela de ``user_id`` para simulações anônimas — não são persistidas.
_USER_ID_ANONIMO = "desconhecido"

# Indicadores macroeconômicos lidos para parametrizar a simulação (Requisito 4.1).
INDICADORES_PADRAO: tuple[str, ...] = ("SELIC", "CDI", "IPCA", "DOLAR")

# Mensagem exibida quando não há dados de mercado suficientes (Requisito 4.4).
MSG_INDISPONIVEL = "A análise de risco está temporariamente indisponível."

# Volatilidade anual base (%) por classe de ativo. Heurística do protótipo:
# renda fixa é a mais estável e cripto a mais volátil.
VOLATILIDADE_BASE: dict[Tipos_Ativo, float] = {
    Tipos_Ativo.RENDA_FIXA: 2.0,
    Tipos_Ativo.FII: 12.0,
    Tipos_Ativo.RENDA_VARIAVEL: 22.0,
    Tipos_Ativo.CRIPTO: 60.0,
}

# Prêmio de retorno anual (%) adicionado à taxa de referência por classe de
# ativo. Renda fixa acompanha a taxa de referência (prêmio zero).
PREMIO_RETORNO: dict[Tipos_Ativo, float] = {
    Tipos_Ativo.RENDA_FIXA: 0.0,
    Tipos_Ativo.FII: 4.0,
    Tipos_Ativo.RENDA_VARIAVEL: 6.0,
    Tipos_Ativo.CRIPTO: 15.0,
}

# Peso da inflação (IPCA) como componente de estresse na volatilidade.
FATOR_ESTRESSE_IPCA = 0.5

# Limiares de volatilidade anual (%) para classificação do nível de risco.
LIMIAR_VOLATILIDADE_BAIXO = 8.0
LIMIAR_VOLATILIDADE_MEDIO = 30.0


class DadosMercadoIndisponiveisError(RuntimeError):
    """Sinaliza ausência de dados de mercado para calcular o risco (Req 4.4)."""


# ---------------------------------------------------------------------------
# Lógica pura (sem I/O) — alvo dos property tests 6.2/6.3
# ---------------------------------------------------------------------------
def taxa_referencia(indicadores: dict[str, float]) -> float | None:
    """Retorna a taxa de referência anual (%) a partir dos indicadores.

    Prefere o CDI e recorre à Selic quando o CDI não está disponível. Retorna
    ``None`` quando nenhuma taxa de referência foi obtida — condição que
    caracteriza indisponibilidade de dados de mercado (Requisito 4.4).
    """
    for nome in ("CDI", "SELIC"):
        valor = indicadores.get(nome)
        if valor is not None:
            return float(valor)
    return None


def calcular_taxa_anual(tipo_ativo: Tipos_Ativo, indicadores: dict[str, float]) -> float:
    """Calcula a taxa de retorno anual esperada (%) para a classe de ativo.

    Soma o prêmio de retorno da classe (:data:`PREMIO_RETORNO`) à taxa de
    referência macroeconômica (Requisito 6.1). Exige dados de mercado.
    """
    referencia = taxa_referencia(indicadores)
    if referencia is None:
        raise DadosMercadoIndisponiveisError(MSG_INDISPONIVEL)
    return referencia + PREMIO_RETORNO[tipo_ativo]


def calcular_volatilidade(tipo_ativo: Tipos_Ativo, indicadores: dict[str, float]) -> float:
    """Calcula a volatilidade anual (%) da simulação (Requisito 4.1).

    Parte da volatilidade base da classe de ativo e adiciona um componente de
    estresse proporcional à inflação (IPCA), quando disponível. O resultado é
    sempre não negativo.
    """
    base = VOLATILIDADE_BASE[tipo_ativo]
    ipca = indicadores.get("IPCA")
    estresse = max(0.0, float(ipca)) * FATOR_ESTRESSE_IPCA if ipca is not None else 0.0
    return round(base + estresse, 4)


def classificar_risco(volatilidade: float) -> Nivel_Risco:
    """Classifica o nível de risco a partir da volatilidade (Requisito 4.2).

    - volatilidade < 8%   → BAIXO
    - 8% ≤ volatilidade < 30% → MEDIO
    - volatilidade ≥ 30%  → ALTO
    """
    if volatilidade < LIMIAR_VOLATILIDADE_BAIXO:
        return Nivel_Risco.BAIXO
    if volatilidade < LIMIAR_VOLATILIDADE_MEDIO:
        return Nivel_Risco.MEDIO
    return Nivel_Risco.ALTO


def calcular_cenarios(
    valor_inicial: float,
    prazo_meses: int,
    taxa_anual: float,
    volatilidade: float,
) -> tuple[float, float, float]:
    """Projeta ``(pessimista, realista, otimista)`` para a simulação.

    O cenário realista usa juros compostos sobre o prazo; os cenários pessimista
    e otimista aplicam um desvio proporcional à volatilidade e à raiz do horizonte
    (``√anos``). O desvio não negativo e o piso em zero garantem a ordem
    pessimista ≤ realista ≤ otimista (Propriedade 4 / Requisito 6.2), preservada
    também após o arredondamento (que é monotônico).
    """
    anos = prazo_meses / 12.0
    realista = max(0.0, valor_inicial * (1.0 + taxa_anual / 100.0) ** anos)
    desvio = (volatilidade / 100.0) * math.sqrt(anos)
    pessimista = max(0.0, realista * (1.0 - desvio))
    otimista = realista * (1.0 + desvio)
    return round(pessimista, 2), round(realista, 2), round(otimista, 2)


def calcular_exposicao(valor_inicial: float, cenario_pessimista: float) -> float:
    """Calcula a exposição financeira (perda potencial) da simulação (Req 4.1).

    Corresponde ao quanto o investidor pode perder no cenário pessimista em
    relação ao valor aplicado. Nunca é negativa: quando o pior cenário ainda
    supera o valor inicial, a exposição é zero.
    """
    return round(max(0.0, valor_inicial - cenario_pessimista), 2)


def analisar_simulacao(
    *,
    user_id: str,
    timestamp: str,
    valor_inicial: float,
    prazo_meses: int,
    tipo_ativo: Tipos_Ativo,
    indicadores: dict[str, float],
) -> tuple[Simulacao, AnaliseRisco]:
    """Orquestra o cálculo puro e devolve a ``Simulacao`` e a ``AnaliseRisco``.

    Levanta :class:`DadosMercadoIndisponiveisError` quando faltam dados de
    mercado (Requisito 4.4). A ``Simulacao`` retornada já inclui o disclaimer
    obrigatório (Requisito 6.3) e é validada — garantindo a ordem dos cenários
    (Propriedade 4) — deixando a lógica pronta para a persistência da Tarefa 6.8.
    """
    taxa_anual = calcular_taxa_anual(tipo_ativo, indicadores)
    volatilidade = calcular_volatilidade(tipo_ativo, indicadores)
    pessimista, realista, otimista = calcular_cenarios(
        valor_inicial, prazo_meses, taxa_anual, volatilidade
    )
    exposicao = calcular_exposicao(valor_inicial, pessimista)
    analise = AnaliseRisco(
        classificacao=classificar_risco(volatilidade),
        volatilidade=volatilidade,
        exposicao=exposicao,
    )
    simulacao = Simulacao(
        user_id=user_id,
        timestamp=timestamp,
        valor_inicial=valor_inicial,
        prazo_meses=prazo_meses,
        tipo_ativo=tipo_ativo,
        cenario_pessimista=pessimista,
        cenario_realista=realista,
        cenario_otimista=otimista,
    )
    # Valida parâmetros, ordem dos cenários (Propriedade 4) e disclaimer (Req 6.3).
    validar_simulacao(simulacao)
    return simulacao, analise


def deve_persistir(user_id: str) -> bool:
    """Indica se a simulação deve ser gravada no ``Historico`` (Requisito 2.6).

    Apenas usuários reais são persistidos: simulações anônimas (``user_id``
    vazio ou igual ao sentinela :data:`_USER_ID_ANONIMO`) não são gravadas.
    """
    return bool(user_id) and user_id != _USER_ID_ANONIMO


def montar_item_historico(simulacao: Simulacao, analise: AnaliseRisco) -> dict[str, Any]:
    """Monta o item consultável da simulação para o ``Historico`` (Requisito 2.6).

    O item usa a partition key ``userId`` e a sort key ``timestamp`` (o mesmo
    timestamp gerado na simulação), permitindo consulta por usuário ordenada no
    tempo. O conteúdo reúne ``to_dict(simulacao)`` e a análise de risco; um
    marcador ``tipo_registro`` distingue simulações de recomendações na tabela.
    Função pura (sem I/O nem conversão para DynamoDB).
    """
    return {
        "userId": simulacao.user_id,
        "timestamp": simulacao.timestamp,
        "tipo_registro": TIPO_REGISTRO_SIMULACAO,
        "simulacao": to_dict(simulacao),
        "analise_risco": to_dict(analise),
    }


# ---------------------------------------------------------------------------
# Extração e validação dos parâmetros de entrada
# ---------------------------------------------------------------------------
def _extrair_parametros(event: dict[str, Any]) -> dict[str, Any]:
    """Extrai e valida os parâmetros da simulação a partir do ``event``.

    Aceita ``valor``/``valor_inicial``, ``prazo``/``prazo_meses`` e ``tipo_ativo``.
    Lança :class:`ErroValidacao` (de ``shared``) com mensagem específica quando
    um parâmetro está ausente ou inválido.
    """
    valor_bruto = event.get("valor_inicial", event.get("valor"))
    if isinstance(valor_bruto, bool) or not isinstance(valor_bruto, (int, float)):
        raise ErroValidacao("O campo 'valor_inicial' deve ser numérico.")
    valor_inicial = float(valor_bruto)
    if valor_inicial < 0:
        raise ErroValidacao("O campo 'valor_inicial' não pode ser negativo.")

    prazo_bruto = event.get("prazo_meses", event.get("prazo"))
    if isinstance(prazo_bruto, bool) or not isinstance(prazo_bruto, int):
        raise ErroValidacao("O campo 'prazo_meses' deve ser um inteiro.")
    if prazo_bruto <= 0:
        raise ErroValidacao("O campo 'prazo_meses' deve ser maior que zero.")

    tipo_bruto = event.get("tipo_ativo")
    try:
        tipo_ativo = Tipos_Ativo(tipo_bruto)
    except ValueError as exc:
        validos = ", ".join(membro.value for membro in Tipos_Ativo)
        raise ErroValidacao(
            f"O campo 'tipo_ativo' deve ser um de: {validos}."
        ) from exc

    # user_id é opcional nesta etapa (persistência é a Tarefa 6.8).
    user_id = event.get("user_id")
    user_id = str(user_id) if user_id else "desconhecido"

    return {
        "user_id": user_id,
        "valor_inicial": valor_inicial,
        "prazo_meses": prazo_bruto,
        "tipo_ativo": tipo_ativo,
    }


# ---------------------------------------------------------------------------
# Acesso ao DynamoDB (I/O) — somente leitura de ``EconomicIndicators``
# ---------------------------------------------------------------------------
def _para_float(valor: Any) -> float | None:
    """Converte números do DynamoDB (``Decimal``) para ``float`` com segurança."""
    if valor is None or isinstance(valor, bool):
        return None
    if isinstance(valor, Decimal):
        return float(valor)
    if isinstance(valor, (int, float)):
        return float(valor)
    return None


def _obter_tabela():
    """Devolve o recurso de tabela ``EconomicIndicators`` (nome via env var)."""
    nome_tabela = os.environ.get(_ENV_TABELA, _TABELA_PADRAO)
    dynamodb = boto3.resource("dynamodb")
    return dynamodb.Table(nome_tabela)


def consultar_valor_recente(tabela, indicator_id: str) -> float | None:
    """Lê o valor mais recente de um indicador (ordem decrescente por ``date``).

    Retorna ``None`` quando o indicador não existe ou não possui valor numérico.
    Operação exclusivamente de leitura.
    """
    resposta = tabela.query(
        KeyConditionExpression=Key(_ATRIBUTO_PK).eq(indicator_id),
        ScanIndexForward=False,
        Limit=1,
    )
    itens = resposta.get("Items", [])
    if not itens:
        return None
    # Preferir a taxa anualizada (base canônica a.a.) quando disponível; a
    # simulação trabalha em base anual. Cai para ``valor`` (ex.: preço do dólar).
    anual = _para_float(itens[0].get("valor_anualizado"))
    return anual if anual is not None else _para_float(itens[0].get("valor"))


def ler_indicadores(tabela, indicator_ids: tuple[str, ...] = INDICADORES_PADRAO) -> dict[str, float]:
    """Lê os valores mais recentes dos indicadores solicitados (somente leitura).

    Indicadores sem dado disponível são omitidos do dicionário retornado.
    """
    valores: dict[str, float] = {}
    for indicator_id in indicator_ids:
        valor = consultar_valor_recente(tabela, indicator_id)
        if valor is not None:
            valores[indicator_id] = valor
    return valores


def _resolver_indicadores(event: dict[str, Any]) -> dict[str, float]:
    """Obtém os indicadores do ``event`` (injeção) ou lê do DynamoDB.

    Permite passar ``indicadores`` no evento (útil para testes/execução local).
    Falhas de leitura no DynamoDB são tratadas como indisponibilidade de dados
    de mercado (retorna dicionário vazio), acionando o fluxo do Requisito 4.4.
    """
    injetados = event.get("indicadores")
    if isinstance(injetados, dict):
        valores: dict[str, float] = {}
        for chave, valor in injetados.items():
            convertido = _para_float(valor)
            if convertido is not None:
                valores[str(chave)] = convertido
        return valores

    try:
        return ler_indicadores(_obter_tabela())
    except Exception as exc:  # noqa: BLE001 - falha de leitura = dados indisponíveis
        logger.warning("Falha ao ler indicadores de mercado: %s", exc)
        return {}


# ---------------------------------------------------------------------------
# Persistência da simulação no ``Historico`` (I/O) — Tarefa 6.8 / Requisito 2.6
# ---------------------------------------------------------------------------
def _para_item_dynamodb(dados: Any) -> Any:
    """Converte ``float`` em ``Decimal`` (exigência do DynamoDB via resource)."""
    return json.loads(json.dumps(dados), parse_float=Decimal)


def _obter_tabela_historico():
    """Devolve o recurso da tabela ``Historico`` (nome via env var)."""
    nome_tabela = os.environ.get(_ENV_TABELA_HISTORICO, _TABELA_HISTORICO_PADRAO)
    dynamodb = boto3.resource("dynamodb")
    return dynamodb.Table(nome_tabela)


def persistir_simulacao(simulacao: Simulacao, analise: AnaliseRisco) -> bool:
    """Grava a simulação no ``Historico`` de forma resiliente (Requisito 2.6).

    Monta o item consultável (PK ``userId`` / SK ``timestamp``), converte os
    ``float`` para ``Decimal`` e escreve na tabela. Retorna ``True`` quando a
    gravação é concluída. Uma falha de escrita é registrada em log e devolve
    ``False`` — nunca propaga a exceção — para não derrubar a resposta da
    simulação. Não faz filtragem por usuário: cabe ao chamador decidir via
    :func:`deve_persistir`.
    """
    item = _para_item_dynamodb(montar_item_historico(simulacao, analise))
    try:
        _obter_tabela_historico().put_item(Item=item)
        return True
    except Exception as exc:  # noqa: BLE001 - falha ao persistir não derruba a resposta
        logger.warning("Falha ao persistir simulação no Historico: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Ponto de entrada
# ---------------------------------------------------------------------------
def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Ponto de entrada da Lambda (acionada pelo Agente_Risco).

    ``event``:
        - ``valor_inicial`` (ou ``valor``): valor aplicado (numérico ≥ 0).
        - ``prazo_meses`` (ou ``prazo``): horizonte em meses (inteiro > 0).
        - ``tipo_ativo``: uma das classes de ``Tipos_Ativo``.
        - ``user_id`` (opcional): quando corresponde a um usuário real, a
          simulação é persistida no ``Historico`` (Requisito 2.6); ausente ou
          ``desconhecido`` resulta em simulação anônima (não gravada).
        - ``indicadores`` (opcional): dicionário de indicadores para injeção;
          quando ausente, os dados são lidos de ``EconomicIndicators``.

    Retorna a simulação com os três cenários, a análise de risco e o disclaimer
    (Requisitos 4.1, 4.2, 6.1, 6.2, 6.3), além da flag ``persistido`` indicando
    se a simulação foi gravada no ``Historico`` (Requisito 2.6). Recalcula a cada
    invocação, atendendo ao recálculo por mudança de parâmetros (Requisito 6.4).
    Quando faltam dados de mercado, retorna ``disponivel = False`` com a mensagem
    de indisponibilidade (Requisito 4.4).
    """
    event = event or {}
    log_evento(logger, "invocacao_recebida", context=context)

    try:
        parametros = _extrair_parametros(event)
    except ErroValidacao as exc:
        return {"disponivel": False, "erro": str(exc)}

    indicadores = _resolver_indicadores(event)

    timestamp = datetime.now(timezone.utc).isoformat()
    try:
        simulacao, analise = analisar_simulacao(
            user_id=parametros["user_id"],
            timestamp=timestamp,
            valor_inicial=parametros["valor_inicial"],
            prazo_meses=parametros["prazo_meses"],
            tipo_ativo=parametros["tipo_ativo"],
            indicadores=indicadores,
        )
    except DadosMercadoIndisponiveisError:
        return {"disponivel": False, "mensagem": MSG_INDISPONIVEL}

    # Persiste a simulação no ``Historico`` para usuários reais (Requisito 2.6).
    # Simulações anônimas não são gravadas; falha ao persistir não derruba a
    # resposta (resiliência) — apenas reflete-se na flag ``persistido``.
    persistido = False
    if deve_persistir(parametros["user_id"]):
        persistido = persistir_simulacao(simulacao, analise)

    log_evento(
        logger, "invocacao_concluida", context=context, persistido=persistido
    )
    return {
        "disponivel": True,
        "simulacao": to_dict(simulacao),
        "analise_risco": to_dict(analise),
        "disclaimer": simulacao.disclaimer,
        "gerado_em": timestamp,
        "persistido": persistido,
    }
