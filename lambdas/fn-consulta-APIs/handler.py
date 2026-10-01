"""Função Lambda ``fn-consulta-APIs`` (pipeline assíncrono — apenas escrita).

Aciona: EventBridge (a cada 5 minutos).

Responsabilidades:
  - Consultar APIs externas — BCB (Selic, IPCA, CDI, Dólar) e B3 (cotações) —
    e **escrever** os indicadores na tabela DynamoDB ``EconomicIndicators``.
  - Verificar os limiares de alerta configurados pelos usuários (``Users``) e
    gravar notificações in-app quando a variação exceder o limiar (Req 8.2/8.4).
  - Fallback (Req 14.2 / Propriedade 9): em indisponibilidade da API, manter o
    último valor em cache e registrar o timestamp da última atualização.

Propriedade 10: em relação a ``EconomicIndicators`` esta função **apenas
escreve** (nunca lê indicadores). A leitura de indicadores para os agentes é
responsabilidade de ``fn-consulta-indicadores`` (Tarefa 5.7). A única leitura
feita aqui é na tabela ``Users`` (limiares) — cujas notificações também são
gravadas de volta.

O módulo separa a lógica pura (parsing, cálculo de variação, detecção de
alertas e decisão de fallback) das operações de I/O (rede e DynamoDB) para
facilitar os testes baseados em propriedades (Tarefas 5.4, 5.5 e 5.6).
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import boto3

from shared import Indicador, LimiarAlerta, log_evento, to_dict, validar_indicador

try:  # ``requests`` é vendorizado no artefato; sua ausência não deve quebrar o import.
    import requests
except ImportError:  # pragma: no cover - só ocorre fora do artefato de deploy
    requests = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)

# ---------------------------------------------------------------------------
# Configuração (nomes de tabela por env var; endpoints e timeout das APIs)
# ---------------------------------------------------------------------------
# Tempo máximo (segundos) de espera por resposta das APIs externas.
TIMEOUT_API_SEGUNDOS = float(os.getenv("TIMEOUT_API_SEGUNDOS", "5"))

# Séries do Sistema Gerenciador de Séries Temporais (SGS) do Banco Central.
# indicator_id → código da série SGS.
SERIES_BCB: dict[str, int] = {
    "SELIC": 432,  # Meta Selic definida pelo Copom (% a.a.)
    "IPCA": 433,   # IPCA — variação mensal (%)
    "CDI": 12,     # Taxa CDI (% a.d.)
    "DOLAR": 1,    # Dólar comercial (venda)
}

# Unidade do valor bruto de cada série do BCB (bases diferentes por série).
# Usada para anualizar tudo para % a.a. (base canônica das simulações/exibição).
UNIDADE_BCB: dict[str, str] = {
    "SELIC": "a.a.",  # já é ao ano
    "IPCA": "a.m.",   # variação mensal
    "CDI": "a.d.",    # taxa ao dia
    "DOLAR": "preco",  # preço, não é taxa
}

# Séries do SGS que já trazem uma medida anual/acumulada própria, usada como
# ``valor_anualizado`` no lugar da anualização aproximada. IPCA: variação
# acumulada em 12 meses (série 13522) — a inflação anual "de fato", em vez de
# compor um único mês (que distorce, sobretudo em meses negativos).
SERIE_ANUAL_BCB: dict[str, int] = {"IPCA": 13522}

# Dias úteis por ano (convenção de mercado BR para anualizar taxas ao dia).
_DIAS_UTEIS_ANO = 252


def anualizar(valor: float, unidade: str) -> float | None:
    """Converte uma taxa para % efetiva ao ano (a.a.); ``None`` se não for taxa.

    - ``a.a.``: retorna o próprio valor.
    - ``a.m.``: ``((1 + i)^12 - 1)`` com ``i = valor/100``.
    - ``a.d.``: ``((1 + i)^252 - 1)`` (252 dias úteis).
    - ``preco`` (ou desconhecida): ``None`` (não é taxa).
    """
    if unidade == "a.a.":
        return round(valor, 6)
    taxa = valor / 100.0
    if unidade == "a.m.":
        return round(((1.0 + taxa) ** 12 - 1.0) * 100.0, 6)
    if unidade == "a.d.":
        return round(((1.0 + taxa) ** _DIAS_UTEIS_ANO - 1.0) * 100.0, 6)
    return None
URL_SGS = (
    "https://api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo}/dados/ultimos/2?formato=json"
)

# Endpoint opcional para cotações da B3 (configurável por ambiente). Quando não
# definido, a etapa da B3 é ignorada sem afetar o restante do pipeline.
URL_B3 = os.getenv("B3_API_URL", "").strip()

# Cache em memória (persiste entre invocações "quentes" da Lambda). Guarda o
# último Indicador conhecido por indicator_id para servir de fallback quando a
# API externa estiver indisponível (Req 14.2 / Propriedade 9).
_CACHE_INDICADORES: dict[str, Indicador] = {}


def _nome_tabela_indicadores() -> str:
    """Nome da tabela de indicadores (env ``TABELA_INDICADORES``)."""
    return os.getenv("TABELA_INDICADORES", "EconomicIndicators")


def _nome_tabela_users() -> str:
    """Nome da tabela de usuários (env ``TABELA_USERS``)."""
    return os.getenv("TABELA_USERS", "Users")


def _recurso_dynamodb():
    """Cria o ``service resource`` do DynamoDB (lazy, para respeitar mocks/env)."""
    return boto3.resource("dynamodb")


@dataclass
class ResultadoIndicador:
    """Resultado da tentativa de obter um indicador (dados novos ou fallback)."""

    indicator_id: str
    indicador: Indicador | None
    origem: str  # "api" | "cache" | "indisponivel"
    data_atualizacao: str | None

# ---------------------------------------------------------------------------
# Lógica pura (sem rede nem AWS) — alvo dos property tests 5.4/5.5/5.6
# ---------------------------------------------------------------------------
def _timestamp_atual() -> str:
    """Timestamp ISO-8601 (UTC) do momento da execução."""
    return datetime.now(timezone.utc).isoformat()


def calcular_variacao_percentual(
    valor_atual: float, valor_anterior: float | None
) -> float | None:
    """Variação percentual entre o valor anterior e o atual.

    Retorna ``None`` quando não há valor anterior comparável (primeiro ponto da
    série) ou quando o valor anterior é zero (divisão indefinida).
    """
    if valor_anterior is None or valor_anterior == 0:
        return None
    return (valor_atual - valor_anterior) / valor_anterior * 100.0


def parsear_serie_bcb(payload: list[dict[str, Any]]) -> tuple[float, float | None]:
    """Extrai ``(valor_atual, valor_anterior)`` de uma série SGS do BCB.

    O SGS retorna uma lista ordenada de observações ``{"data", "valor"}``; o
    valor atual é a última observação e o anterior é a penúltima (se houver).
    """
    if not payload:
        raise ValueError("Série do BCB vazia.")
    valores = [float(str(obs["valor"]).replace(",", ".")) for obs in payload]
    valor_atual = valores[-1]
    valor_anterior = valores[-2] if len(valores) >= 2 else None
    return valor_atual, valor_anterior


def parsear_cotacoes_b3(payload: list[dict[str, Any]]) -> list[tuple[str, float]]:
    """Converte a resposta da B3 numa lista de ``(ticker, preço)``.

    Aceita chaves alternativas comuns (``ticker``/``codigo``/``symbol`` e
    ``price``/``preco``/``valor``); itens incompletos são ignorados.
    """
    cotacoes: list[tuple[str, float]] = []
    for item in payload or []:
        ticker = item.get("ticker") or item.get("codigo") or item.get("symbol")
        preco = item.get("price") or item.get("preco") or item.get("valor")
        if ticker is None or preco is None:
            continue
        cotacoes.append((str(ticker), float(str(preco).replace(",", "."))))
    return cotacoes


def montar_indicador(
    indicator_id: str,
    valor_atual: float,
    valor_anterior: float | None,
    timestamp: str,
    unidade: str = "preco",
    valor_anualizado: float | None = None,
) -> Indicador:
    """Monta e valida um ``Indicador`` (variação + unidade + valor anualizado).

    Quando ``valor_anualizado`` é informado, usa esse valor (ex.: IPCA acumulado
    em 12 meses); caso contrário, calcula pela anualização da ``unidade``.
    """
    variacao = calcular_variacao_percentual(valor_atual, valor_anterior)
    anualizado = (
        round(valor_anualizado, 6)
        if valor_anualizado is not None
        else anualizar(valor_atual, unidade)
    )
    indicador = Indicador(
        indicator_id=indicator_id,
        date=timestamp,
        valor=valor_atual,
        valor_anterior=valor_anterior,
        variacao_percentual=variacao,
        unidade=unidade,
        valor_anualizado=anualizado,
    )
    return validar_indicador(indicador)

def variacao_excede_limiar(variacao_percentual: float, limiar_percentual: float) -> bool:
    """Propriedade 5: o alerta dispara se, e somente se, |variação| > limiar."""
    return abs(variacao_percentual) > limiar_percentual


def _montar_notificacao(
    indicador: Indicador, limiar: LimiarAlerta, timestamp: str
) -> dict[str, Any]:
    """Monta a notificação in-app de alerta de mercado (campo em ``Users``)."""
    return {
        "id": str(uuid.uuid4()),
        "tipo": "ALERTA_MERCADO",
        "indicatorId": indicador.indicator_id,
        "variacao_percentual": indicador.variacao_percentual,
        "limiar_percentual": limiar.limiar_percentual,
        "mensagem": (
            f"O indicador {indicador.indicator_id} variou "
            f"{indicador.variacao_percentual:.2f}%, excedendo o limiar de "
            f"{limiar.limiar_percentual:.2f}% configurado."
        ),
        "timestamp": timestamp,
        "lida": False,
    }


def avaliar_alertas(
    limiares: list[LimiarAlerta],
    indicadores: dict[str, Indicador],
    timestamp: str,
) -> list[dict[str, Any]]:
    """Gera notificações in-app para os limiares excedidos (Req 8.2/8.4).

    Percorre os limiares configurados pelo usuário e, para cada indicador com
    variação conhecida cujo módulo excede o limiar, monta uma notificação
    (Propriedade 5).
    """
    notificacoes: list[dict[str, Any]] = []
    for limiar in limiares:
        indicador = indicadores.get(limiar.indicator_id)
        if indicador is None or indicador.variacao_percentual is None:
            continue
        if variacao_excede_limiar(indicador.variacao_percentual, limiar.limiar_percentual):
            notificacoes.append(_montar_notificacao(indicador, limiar, timestamp))
    return notificacoes


def resolver_com_fallback(
    indicator_id: str,
    indicador_novo: Indicador | None,
    cache: dict[str, Indicador],
) -> ResultadoIndicador:
    """Decide o Indicador efetivo aplicando fallback com cache (Propriedade 9).

    - Sucesso da API: usa o dado novo (``origem="api"``).
    - Falha com cache disponível: reusa o último valor em cache e informa o
      timestamp da última atualização (``origem="cache"``, Req 14.2).
    - Falha sem cache: indicador indisponível (``origem="indisponivel"``).
    """
    if indicador_novo is not None:
        return ResultadoIndicador(indicator_id, indicador_novo, "api", indicador_novo.date)
    em_cache = cache.get(indicator_id)
    if em_cache is not None:
        return ResultadoIndicador(indicator_id, em_cache, "cache", em_cache.date)
    return ResultadoIndicador(indicator_id, None, "indisponivel", None)

# ---------------------------------------------------------------------------
# Consulta às APIs externas (rede) — cada etapa é isolável e tolerante a falhas
# ---------------------------------------------------------------------------
def _consultar_serie_bcb(session: Any, codigo: int) -> list[dict[str, Any]]:
    """Chama o SGS do BCB para uma série e devolve o payload JSON."""
    resposta = session.get(URL_SGS.format(codigo=codigo), timeout=TIMEOUT_API_SEGUNDOS)
    resposta.raise_for_status()
    return resposta.json()


def consultar_indicadores_bcb(
    timestamp: str, session: Any | None = None
) -> dict[str, Indicador]:
    """Consulta as séries do BCB e retorna os indicadores obtidos com sucesso.

    Cada série é consultada isoladamente: a falha de uma não impede as demais.
    Séries que falharem simplesmente não aparecem no dicionário retornado — o
    fallback é aplicado depois, em ``resolver_com_fallback``.
    """
    if requests is None:
        logger.warning("Biblioteca 'requests' indisponível; etapa BCB ignorada.")
        return {}
    sessao = session or requests.Session()
    obtidos: dict[str, Indicador] = {}
    for indicator_id, codigo in SERIES_BCB.items():
        try:
            payload = _consultar_serie_bcb(sessao, codigo)
            valor_atual, valor_anterior = parsear_serie_bcb(payload)
            # Para indicadores com série anual/acumulada própria (ex.: IPCA 12m),
            # usa esse valor como anualizado; a falha nessa série extra não impede
            # o indicador (mantém a anualização aproximada como reserva).
            anual_override: float | None = None
            codigo_anual = SERIE_ANUAL_BCB.get(indicator_id)
            if codigo_anual is not None:
                try:
                    va_atual, _ = parsear_serie_bcb(
                        _consultar_serie_bcb(sessao, codigo_anual)
                    )
                    anual_override = va_atual
                except Exception as exc:  # noqa: BLE001 - série anual é complementar
                    logger.warning(
                        "Falha na série anual BCB %s (%s): %s",
                        indicator_id, codigo_anual, exc,
                    )
            obtidos[indicator_id] = montar_indicador(
                indicator_id,
                valor_atual,
                valor_anterior,
                timestamp,
                UNIDADE_BCB.get(indicator_id, "preco"),
                valor_anualizado=anual_override,
            )
        except Exception as exc:  # noqa: BLE001 - falha por indicador é tolerada
            logger.warning("Falha ao consultar série BCB %s (%s): %s", indicator_id, codigo, exc)
    return obtidos


def consultar_cotacoes_b3(
    timestamp: str, session: Any | None = None
) -> dict[str, Indicador]:
    """Consulta cotações da B3 (best-effort) e retorna indicadores por ticker.

    Requer a variável de ambiente ``B3_API_URL``. Sem endpoint configurado ou em
    caso de falha, retorna vazio e deixa o fallback assumir.
    """
    if requests is None or not URL_B3:
        return {}
    sessao = session or requests.Session()
    try:
        resposta = sessao.get(URL_B3, timeout=TIMEOUT_API_SEGUNDOS)
        resposta.raise_for_status()
        cotacoes = parsear_cotacoes_b3(resposta.json())
    except Exception as exc:  # noqa: BLE001 - B3 é best-effort; fallback assume
        logger.warning("Falha ao consultar cotações da B3: %s", exc)
        return {}
    return {
        ticker: montar_indicador(ticker, preco, None, timestamp, "preco")
        for ticker, preco in cotacoes
    }

# ---------------------------------------------------------------------------
# Persistência (DynamoDB) — escrita de indicadores e notificações
# ---------------------------------------------------------------------------
def _para_item_dynamodb(dados: Any) -> Any:
    """Converte ``float`` em ``Decimal`` (exigência do DynamoDB via resource)."""
    return json.loads(json.dumps(dados), parse_float=Decimal)


def persistir_indicador(tabela: Any, indicador: Indicador) -> None:
    """Escreve um Indicador em ``EconomicIndicators`` (única escrita da função).

    Mapeia o modelo para os atributos-chave da tabela (``indicatorId`` +
    ``date``). Operação exclusivamente de escrita (Propriedade 10).
    """
    dados = to_dict(indicador)
    dados.pop("indicator_id", None)  # substituído pela partition key ``indicatorId``
    item = _para_item_dynamodb(dados)
    item["indicatorId"] = indicador.indicator_id
    tabela.put_item(Item=item)


def _parsear_limiares(item_user: dict[str, Any]) -> list[LimiarAlerta]:
    """Reconstrói os ``LimiarAlerta`` configurados por um usuário."""
    limiares: list[LimiarAlerta] = []
    for bruto in item_user.get("limiares_alerta", []) or []:
        try:
            limiares.append(
                LimiarAlerta(
                    indicator_id=str(bruto["indicator_id"]),
                    limiar_percentual=float(bruto["limiar_percentual"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return limiares


def _gravar_notificacoes(tabela_users: Any, user_id: str, novas: list[dict[str, Any]]) -> None:
    """Anexa notificações à lista ``notificacoes`` do usuário (alerta in-app)."""
    tabela_users.update_item(
        Key={"userId": user_id},
        UpdateExpression=(
            "SET notificacoes = list_append(if_not_exists(notificacoes, :vazio), :novas)"
        ),
        ExpressionAttributeValues={":novas": _para_item_dynamodb(novas), ":vazio": []},
    )


def notificar_usuarios(
    tabela_users: Any, indicadores: dict[str, Indicador], timestamp: str
) -> int:
    """Lê limiares em ``Users``, avalia alertas e grava notificações in-app.

    Retorna o total de notificações gravadas. Leitura e escrita ocorrem apenas
    em ``Users`` (não em ``EconomicIndicators``), preservando a Propriedade 10.
    """
    total = 0
    ultima_chave: dict[str, Any] | None = None
    while True:
        resposta = (
            tabela_users.scan(ExclusiveStartKey=ultima_chave)
            if ultima_chave
            else tabela_users.scan()
        )
        for item in resposta.get("Items", []):
            limiares = _parsear_limiares(item)
            if not limiares:
                continue
            notificacoes = avaliar_alertas(limiares, indicadores, timestamp)
            if notificacoes:
                _gravar_notificacoes(tabela_users, item["userId"], notificacoes)
                total += len(notificacoes)
        ultima_chave = resposta.get("LastEvaluatedKey")
        if not ultima_chave:
            break
    return total

# ---------------------------------------------------------------------------
# Ponto de entrada (orquestração do pipeline assíncrono)
# ---------------------------------------------------------------------------
def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Ponto de entrada do pipeline assíncrono (EventBridge, a cada 5 min).

    Fluxo:
      1. Consulta APIs externas (BCB + B3), cada etapa isolável e tolerante a falhas.
      2. Resolve cada indicador com fallback de cache (Req 14.2 / Propriedade 9);
         persiste em ``EconomicIndicators`` apenas os dados novos da API.
      3. Verifica limiares em ``Users`` e grava notificações in-app (Req 8.2/8.4).
    """
    log_evento(logger, "pipeline_iniciado", context=context)
    timestamp = _timestamp_atual()
    dynamodb = _recurso_dynamodb()
    tabela_indicadores = dynamodb.Table(_nome_tabela_indicadores())
    tabela_users = dynamodb.Table(_nome_tabela_users())

    # 1. Consulta às APIs externas.
    obtidos: dict[str, Indicador] = {}
    obtidos.update(consultar_indicadores_bcb(timestamp))
    obtidos.update(consultar_cotacoes_b3(timestamp))

    # 2. Resolve cada indicador esperado aplicando fallback com cache.
    indicadores_efetivos: dict[str, Indicador] = {}
    resumo: list[dict[str, Any]] = []
    esperados = set(SERIES_BCB) | set(obtidos) | set(_CACHE_INDICADORES)
    for indicator_id in sorted(esperados):
        resultado = resolver_com_fallback(
            indicator_id, obtidos.get(indicator_id), _CACHE_INDICADORES
        )
        if resultado.indicador is not None:
            indicadores_efetivos[indicator_id] = resultado.indicador
        # Persiste apenas dados novos da API; o fallback mantém o cache já gravado.
        if resultado.origem == "api" and resultado.indicador is not None:
            persistir_indicador(tabela_indicadores, resultado.indicador)
            _CACHE_INDICADORES[indicator_id] = resultado.indicador
        resumo.append(
            {
                "indicatorId": indicator_id,
                "origem": resultado.origem,
                "dataAtualizacao": resultado.data_atualizacao,
            }
        )

    # 3. Verifica limiares e grava notificações in-app.
    total_notificacoes = notificar_usuarios(tabela_users, indicadores_efetivos, timestamp)

    resultado = {
        "timestamp": timestamp,
        "indicadores": resumo,
        "persistidos": sum(1 for item in resumo if item["origem"] == "api"),
        "emFallback": sum(1 for item in resumo if item["origem"] == "cache"),
        "indisponiveis": sum(1 for item in resumo if item["origem"] == "indisponivel"),
        "notificacoesGeradas": total_notificacoes,
    }
    log_evento(
        logger,
        "pipeline_concluido",
        context=context,
        persistidos=resultado["persistidos"],
        em_fallback=resultado["emFallback"],
        indisponiveis=resultado["indisponiveis"],
        notificacoes=total_notificacoes,
    )
    return resultado
