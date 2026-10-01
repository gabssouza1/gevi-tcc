"""Função Lambda ``fn-selecao-ativos``.

Aciona: Agente_Selecao_Ativos (via AgentCore Gateway / tool routing).
Responsabilidade: filtrar ativos incompatíveis com o Perfil_Investidor e
ranqueá-los por relação risco/retorno, devolvendo uma lista ordenada de
sugestões com rentabilidade esperada e prazo.

Lê apenas (nunca escreve):
    - ``Users`` (``TABELA_USERS``)        → tolerância a risco do investidor.
    - ``EconomicIndicators`` (``TABELA_INDICADORES``) → indicadores atuais.

Requisitos cobertos:
    - 3.2 gerar sugestões pertencentes aos ``Tipos_Ativo`` e compatíveis com o
          Perfil_Investidor;
    - 3.3 considerar os Indicadores_Macroeconomicos atuais ao gerar as sugestões;
    - 3.4 apresentar uma lista ordenada com rentabilidade esperada e prazo.

Propriedade 1 (margem de compatibilidade — invariante rígido do design):
    o nível de risco de cada ativo recomendado não pode exceder a tolerância do
    perfil em mais de um nível adjacente (``R.nivelRisco <= P.toleranciaRisco + 1``).

A persistência da recomendação no ``Historico`` NÃO é feita aqui — é a Tarefa 9.9.

A lógica pura (mapeamento de escalas, filtro, estimativa de rentabilidade e
ranqueamento) é mantida separada da camada de I/O (leitura do DynamoDB) para
facilitar testes unitários e o property test (Tarefa 6.7).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key

from shared import (
    AtivoRecomendado,
    Nivel_Risco,
    Perfil_Risco,
    Tipos_Ativo,
    consultar_historico,
    gravar_recomendacao,
    log_erro,
    log_evento,
    montar_item_recomendacao,
    obter_logger,
    to_dict,
)

logger = obter_logger(__name__)

# ---------------------------------------------------------------------------
# Configuração (nomes de tabela via variáveis de ambiente definidas pelo CDK)
# ---------------------------------------------------------------------------
NOME_TABELA_INDICADORES = os.environ.get("TABELA_INDICADORES", "EconomicIndicators")
NOME_TABELA_USERS = os.environ.get("TABELA_USERS", "Users")
# Tabela ``Historico`` (PK ``userId`` + SK ``timestamp``): persistência da
# recomendação (Tarefa 9.9) e consulta do histórico (Tarefa 9.10).
NOME_TABELA_HISTORICO = os.environ.get("TABELA_HISTORICO", "Historico")

# Operação de consulta do histórico (Tarefa 9.10). Sem ``operacao``, a Lambda
# faz a seleção de ativos (comportamento padrão da tool).
_OP_CONSULTAR_HISTORICO = "consultar_historico"

# Atributos de chave das tabelas (ver design/conftest).
_PK_INDICADORES = "indicatorId"
_PK_USERS = "userId"

# Indicadores macroeconômicos usados para estimar rentabilidade (Requisito 3.3).
INDICADORES_RELEVANTES: tuple[str, ...] = ("CDI", "SELIC", "IPCA")

# CDI anual (%) usado como base quando a tabela de indicadores está indisponível.
_CDI_PADRAO_ANUAL = 10.0

# Aliases camelCase (frontend/agente) → snake_case do modelo.
_ALIASES_CAMPOS = {
    "assetId": "asset_id",
    "tipoAtivo": "tipo_ativo",
    "nivelRisco": "nivel_risco",
    "rentabilidadeEsperada": "rentabilidade_esperada",
}

# Códigos de erro retornados ao chamador (Gateway/agente).
COD_DADOS_INVALIDOS = "DADOS_INVALIDOS"
COD_NAO_ENCONTRADO = "NAO_ENCONTRADO"
COD_ERRO_INTERNO = "ERRO_INTERNO"


# ---------------------------------------------------------------------------
# Mapeamento coerente entre Perfil_Risco (tolerância) e Nivel_Risco (ativo)
# ---------------------------------------------------------------------------
# Ambas as escalas usam ordinais 0..2 (CONSERVADOR/BAIXO < MODERADO/MEDIO <
# ARROJADO/ALTO), permitindo comparar tolerância e risco na mesma régua.
_PERFIL_PARA_NIVEL: dict[Perfil_Risco, Nivel_Risco] = {
    Perfil_Risco.CONSERVADOR: Nivel_Risco.BAIXO,
    Perfil_Risco.MODERADO: Nivel_Risco.MEDIO,
    Perfil_Risco.ARROJADO: Nivel_Risco.ALTO,
}

# Nível de risco padrão por classe de ativo, usado quando o candidato não o traz.
_NIVEL_PADRAO_POR_TIPO: dict[Tipos_Ativo, Nivel_Risco] = {
    Tipos_Ativo.RENDA_FIXA: Nivel_Risco.BAIXO,
    Tipos_Ativo.FII: Nivel_Risco.MEDIO,
    Tipos_Ativo.RENDA_VARIAVEL: Nivel_Risco.ALTO,
    Tipos_Ativo.CRIPTO: Nivel_Risco.ALTO,
}


def perfil_para_nivel(tolerancia: Perfil_Risco) -> Nivel_Risco:
    """Converte a tolerância do perfil no ``Nivel_Risco`` equivalente."""
    return _PERFIL_PARA_NIVEL[tolerancia]


def ativo_compativel(nivel_ativo: Nivel_Risco, tolerancia: Perfil_Risco) -> bool:
    """Aplica a margem de compatibilidade da Propriedade 1 (invariante rígido).

    O ativo é compatível quando seu nível de risco não excede a tolerância do
    perfil em mais de um nível adjacente: ``nivel_ativo <= tolerancia + 1`` na
    régua ordinal compartilhada (0 = menor risco/tolerância).
    """
    return nivel_ativo.ordinal <= tolerancia.ordinal + 1


# ---------------------------------------------------------------------------
# Catálogo padrão de candidatos (dentre os Tipos_Ativo) — Requisito 3.2
# ---------------------------------------------------------------------------
# Usado quando o ``event`` não traz uma lista de ativos candidatos (ex.: a saída
# de ``fn-analise-portfolio``). Cada entrada cobre uma classe de ``Tipos_Ativo``.
# O ``premio`` é somado ao CDI anual para estimar a rentabilidade (Requisito 3.3).
_CATALOGO_PADRAO: tuple[dict[str, Any], ...] = (
    {
        "asset_id": "TESOURO_SELIC",
        "tipo_ativo": Tipos_Ativo.RENDA_FIXA,
        "nivel_risco": Nivel_Risco.BAIXO,
        "prazo": "curto",
        "premio": 0.0,
    },
    {
        "asset_id": "CDB_LIQUIDEZ",
        "tipo_ativo": Tipos_Ativo.RENDA_FIXA,
        "nivel_risco": Nivel_Risco.BAIXO,
        "prazo": "medio",
        "premio": 1.5,
    },
    {
        "asset_id": "FII_TIJOLO",
        "tipo_ativo": Tipos_Ativo.FII,
        "nivel_risco": Nivel_Risco.MEDIO,
        "prazo": "longo",
        "premio": 4.0,
    },
    {
        "asset_id": "ACOES_IBOV",
        "tipo_ativo": Tipos_Ativo.RENDA_VARIAVEL,
        "nivel_risco": Nivel_Risco.ALTO,
        "prazo": "longo",
        "premio": 8.0,
    },
    {
        "asset_id": "CRIPTO_BTC",
        "tipo_ativo": Tipos_Ativo.CRIPTO,
        "nivel_risco": Nivel_Risco.ALTO,
        "prazo": "longo",
        "premio": 15.0,
    },
)


# ---------------------------------------------------------------------------
# Estimativa de rentabilidade e score de risco/retorno (lógica pura)
# ---------------------------------------------------------------------------
def _cdi_base(indicadores: dict[str, float]) -> float:
    """Obtém o CDI anual (%) a partir dos indicadores, com fallbacks.

    Preferência: CDI → SELIC (proxy próxima do CDI) → valor padrão. Considerar o
    CDI/SELIC atuais atende ao Requisito 3.3 (usar indicadores macroeconômicos).
    """
    for chave in ("CDI", "SELIC"):
        valor = indicadores.get(chave)
        if valor is not None and valor > 0:
            return float(valor)
    return _CDI_PADRAO_ANUAL


def estimar_rentabilidade(premio: float, indicadores: dict[str, float]) -> float:
    """Estima a rentabilidade anual esperada (%) de um candidato (Requisito 3.3).

    Parte do CDI atual (base de renda fixa) e soma um prêmio de risco por classe
    de ativo. Quanto maior o risco, maior o prêmio esperado.
    """
    return round(_cdi_base(indicadores) + float(premio), 4)


def peso_risco(nivel: Nivel_Risco) -> int:
    """Peso de risco usado no denominador do score (1 = menor risco)."""
    return nivel.ordinal + 1


def score_risco_retorno(ativo: AtivoRecomendado) -> float:
    """Calcula a relação risco/retorno de um ativo (maior é melhor).

    Divide a rentabilidade esperada pelo peso de risco, favorecendo os ativos
    que entregam mais retorno por unidade de risco assumido.
    """
    return ativo.rentabilidade_esperada / peso_risco(ativo.nivel_risco)


# ---------------------------------------------------------------------------
# Normalização de candidatos e seleção (lógica pura, sem I/O)
# ---------------------------------------------------------------------------
def _normalizar_chaves(dados: dict[str, Any]) -> dict[str, Any]:
    """Converte chaves camelCase conhecidas para o snake_case do modelo."""
    return {_ALIASES_CAMPOS.get(chave, chave): valor for chave, valor in dados.items()}


def _coagir_tipo(valor: Any) -> Tipos_Ativo:
    """Interpreta o tipo de ativo (enum ou texto), exigindo um ``Tipos_Ativo``."""
    if isinstance(valor, Tipos_Ativo):
        return valor
    try:
        return Tipos_Ativo(valor)
    except ValueError as exc:
        validos = ", ".join(m.value for m in Tipos_Ativo)
        raise ValueError(
            f"tipo_ativo inválido: {valor!r}. Use um de: {validos}."
        ) from exc


def _coagir_nivel(valor: Any, tipo: Tipos_Ativo) -> Nivel_Risco:
    """Interpreta o nível de risco; sem valor, usa o padrão da classe do ativo."""
    if valor is None:
        return _NIVEL_PADRAO_POR_TIPO[tipo]
    if isinstance(valor, Nivel_Risco):
        return valor
    try:
        return Nivel_Risco(valor)
    except ValueError as exc:
        validos = ", ".join(m.value for m in Nivel_Risco)
        raise ValueError(
            f"nivel_risco inválido: {valor!r}. Use um de: {validos}."
        ) from exc


def construir_candidato(
    dados: dict[str, Any], indicadores: dict[str, float]
) -> AtivoRecomendado:
    """Constrói um :class:`AtivoRecomendado` a partir de um candidato bruto.

    Preenche o nível de risco pelo padrão da classe quando ausente e estima a
    rentabilidade a partir dos indicadores atuais quando não informada
    (Requisitos 3.3, 3.4).
    """
    dados = _normalizar_chaves(dados)
    asset_id = dados.get("asset_id")
    if not isinstance(asset_id, str) or not asset_id.strip():
        raise ValueError("Cada candidato deve ter um 'asset_id' textual não vazio.")

    tipo = _coagir_tipo(dados.get("tipo_ativo"))
    nivel = _coagir_nivel(dados.get("nivel_risco"), tipo)

    rentabilidade = dados.get("rentabilidade_esperada")
    if rentabilidade is None:
        premio = dados.get("premio", 0.0)
        rentabilidade = estimar_rentabilidade(premio, indicadores)
    else:
        rentabilidade = float(rentabilidade)

    prazo = dados.get("prazo") or "medio"
    return AtivoRecomendado(
        asset_id=asset_id.strip(),
        tipo_ativo=tipo,
        nivel_risco=nivel,
        rentabilidade_esperada=rentabilidade,
        prazo=str(prazo),
    )


def montar_candidatos(
    candidatos: list[dict[str, Any]] | None, indicadores: dict[str, float]
) -> list[AtivoRecomendado]:
    """Normaliza a lista de candidatos (usa o catálogo padrão se vazia)."""
    origem = candidatos if candidatos else list(_CATALOGO_PADRAO)
    return [construir_candidato(item, indicadores) for item in origem]


def filtrar_compativeis(
    ativos: list[AtivoRecomendado], tolerancia: Perfil_Risco
) -> list[AtivoRecomendado]:
    """Remove os ativos incompatíveis com o perfil (Propriedade 1, Requisito 3.2)."""
    return [a for a in ativos if ativo_compativel(a.nivel_risco, tolerancia)]


def ranquear(ativos: list[AtivoRecomendado]) -> list[AtivoRecomendado]:
    """Ordena por melhor relação risco/retorno (Requisito 3.4).

    Critérios, em ordem: maior score risco/retorno; em empate, menor nível de
    risco; por fim, ``asset_id`` para um resultado determinístico.
    """
    return sorted(
        ativos,
        key=lambda a: (-score_risco_retorno(a), a.nivel_risco.ordinal, a.asset_id),
    )


def selecionar_ativos(
    tolerancia: Perfil_Risco,
    candidatos: list[dict[str, Any]] | None,
    indicadores: dict[str, float],
) -> list[AtivoRecomendado]:
    """Filtra os candidatos incompatíveis e os ranqueia por risco/retorno.

    Orquestra a lógica pura: normaliza candidatos, aplica a margem de
    compatibilidade da Propriedade 1 e ordena a lista resultante.
    """
    ativos = montar_candidatos(candidatos, indicadores)
    compativeis = filtrar_compativeis(ativos, tolerancia)
    return ranquear(compativeis)


# ---------------------------------------------------------------------------
# Camada de I/O — leitura do DynamoDB (somente leitura)
# ---------------------------------------------------------------------------
class ErroDadosInvalidos(ValueError):
    """Entrada inválida fornecida no ``event``."""


class PerfilNaoEncontradoError(Exception):
    """Nenhum perfil encontrado para o ``user_id`` informado."""


def _para_float(valor: Any) -> float | None:
    """Converte números do DynamoDB (``Decimal``) para ``float`` de forma segura."""
    if valor is None or isinstance(valor, bool):
        return None
    if isinstance(valor, Decimal):
        return float(valor)
    if isinstance(valor, (int, float)):
        return float(valor)
    return None


def _obter_tabela(nome: str):
    """Devolve o recurso de tabela DynamoDB pelo nome informado."""
    return boto3.resource("dynamodb").Table(nome)


def ler_tolerancia_usuario(tabela_users, user_id: str) -> Perfil_Risco:
    """Lê a tolerância a risco do Perfil_Investidor na tabela ``Users``."""
    resposta = tabela_users.get_item(Key={_PK_USERS: user_id})
    item = resposta.get("Item")
    if item is None:
        raise PerfilNaoEncontradoError(
            f"Nenhum perfil encontrado para o id '{user_id}'."
        )
    bruto = item.get("tolerancia_risco") or item.get("toleranciaRisco")
    try:
        return Perfil_Risco(bruto)
    except ValueError as exc:
        raise ErroDadosInvalidos(
            f"tolerância a risco inválida no perfil '{user_id}': {bruto!r}."
        ) from exc


def ler_indicadores_atuais(
    tabela_indicadores, indicator_ids: tuple[str, ...] = INDICADORES_RELEVANTES
) -> dict[str, float]:
    """Lê o valor mais recente de cada indicador (Requisito 3.3, somente leitura).

    Para cada ``indicator_id`` consulta o item de maior ``date`` (sort key) via
    ``ScanIndexForward=False``. Indicadores ausentes são simplesmente omitidos.
    """
    indicadores: dict[str, float] = {}
    for indicator_id in indicator_ids:
        resposta = tabela_indicadores.query(
            KeyConditionExpression=Key(_PK_INDICADORES).eq(indicator_id),
            ScanIndexForward=False,
            Limit=1,
        )
        itens = resposta.get("Items", [])
        if not itens:
            continue
        valor = _para_float(itens[0].get("valor"))
        if valor is not None:
            indicadores[indicator_id] = valor
    return indicadores


# ---------------------------------------------------------------------------
# Respostas padronizadas
# ---------------------------------------------------------------------------
def _resposta_ok(**conteudo: Any) -> dict[str, Any]:
    """Monta uma resposta de sucesso."""
    return {"sucesso": True, **conteudo}


def _resposta_erro(codigo: str, mensagem: str) -> dict[str, Any]:
    """Monta uma resposta de erro estruturada."""
    return {"sucesso": False, "codigo": codigo, "erro": mensagem}


def _extrair_tolerancia(event: dict[str, Any]) -> Perfil_Risco | None:
    """Obtém a tolerância informada diretamente no ``event`` (opcional)."""
    bruto = event.get("tolerancia_risco") or event.get("toleranciaRisco")
    if bruto is None:
        return None
    try:
        return Perfil_Risco(bruto)
    except ValueError as exc:
        raise ErroDadosInvalidos(
            f"tolerancia_risco inválida: {bruto!r}."
        ) from exc


# ---------------------------------------------------------------------------
# Orquestração e ponto de entrada
# ---------------------------------------------------------------------------
def _user_id_do_event(event: dict[str, Any]) -> str | None:
    """Extrai um ``user_id`` não vazio do ``event`` (aceita camelCase)."""
    bruto = event.get("user_id") or event.get("userId")
    return bruto.strip() if isinstance(bruto, str) and bruto.strip() else None


def processar(
    event: dict[str, Any], tabela_users, tabela_indicadores, tabela_historico=None
) -> dict[str, Any]:
    """Executa a seleção de ativos (ou consulta o histórico) com as tabelas dadas.

    Separado do ``handler`` para permitir testes com tabelas simuladas (moto)
    sem depender de variáveis de ambiente globais.

    ``event`` (campos):
        - ``operacao``: ``consultar_historico`` lê o histórico do usuário
          (Tarefa 9.10); ausente, faz a seleção de ativos (comportamento padrão).
        - ``user_id`` / ``userId``: perfil a consultar em ``Users`` (obrigatório
          quando ``tolerancia_risco`` não é fornecida diretamente) e chave do
          histórico/persistência.
        - ``tolerancia_risco``: alternativa a ``user_id`` (usa o valor sem ler
          ``Users``).
        - ``ativos``: lista opcional de candidatos; sem ela, usa o catálogo padrão.
    """
    # Tarefa 9.10 — consulta do histórico (recomendações + simulações).
    if event.get("operacao") == _OP_CONSULTAR_HISTORICO:
        user_id = _user_id_do_event(event)
        if user_id is None:
            return _resposta_erro(
                COD_DADOS_INVALIDOS, "Informe 'user_id' para consultar o histórico."
            )
        if tabela_historico is None:
            return _resposta_erro(COD_ERRO_INTERNO, "Histórico indisponível.")
        itens = consultar_historico(
            tabela_historico, user_id, tipo=event.get("tipo")
        )
        return _resposta_ok(user_id=user_id, historico=itens)

    try:
        tolerancia = _extrair_tolerancia(event)
        user_id = _user_id_do_event(event)
        if tolerancia is None:
            if user_id is None:
                raise ErroDadosInvalidos(
                    "Informe 'user_id' (para ler o perfil) ou 'tolerancia_risco'."
                )
            tolerancia = ler_tolerancia_usuario(tabela_users, user_id)

        candidatos = event.get("ativos")
        if candidatos is not None and not isinstance(candidatos, list):
            raise ErroDadosInvalidos("O campo 'ativos' deve ser uma lista.")

        indicadores = ler_indicadores_atuais(tabela_indicadores)
        selecionados = selecionar_ativos(tolerancia, candidatos, indicadores)
        ativos_serializados = [to_dict(a) for a in selecionados]

        # Tarefa 9.9 — persiste a recomendação no Historico (resiliente) quando
        # há usuário identificado. Falha na escrita não derruba a resposta.
        if user_id is not None and tabela_historico is not None:
            item = montar_item_recomendacao(
                user_id,
                datetime.now(timezone.utc).isoformat(),
                ativos=ativos_serializados,
                tolerancia_risco=tolerancia.value,
                indicadores=indicadores,
            )
            gravar_recomendacao(tabela_historico, item)

        return _resposta_ok(
            tolerancia_risco=tolerancia.value,
            indicadores=indicadores,
            ativos=ativos_serializados,
        )
    except ErroDadosInvalidos as exc:
        return _resposta_erro(COD_DADOS_INVALIDOS, str(exc))
    except ValueError as exc:  # candidatos malformados (tipo/nível inválidos)
        return _resposta_erro(COD_DADOS_INVALIDOS, str(exc))
    except PerfilNaoEncontradoError as exc:
        return _resposta_erro(COD_NAO_ENCONTRADO, str(exc))


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Ponto de entrada da Lambda acionada pelo Agente_Selecao_Ativos."""
    if not isinstance(event, dict):
        return _resposta_erro(COD_DADOS_INVALIDOS, "O event deve ser um objeto JSON.")
    log_evento(
        logger, "invocacao_recebida", context=context, operacao=event.get("operacao")
    )
    try:
        tabela_users = _obter_tabela(NOME_TABELA_USERS)
        tabela_indicadores = _obter_tabela(NOME_TABELA_INDICADORES)
        tabela_historico = _obter_tabela(NOME_TABELA_HISTORICO)
        resposta = processar(event, tabela_users, tabela_indicadores, tabela_historico)
        log_evento(
            logger, "invocacao_concluida", context=context, sucesso=resposta.get("sucesso")
        )
        return resposta
    except Exception as exc:  # noqa: BLE001 — fronteira: nunca vazar exceção crua
        log_erro(logger, "erro_interno", context=context, erro=str(exc))
        return _resposta_erro(
            COD_ERRO_INTERNO, f"Erro interno ao selecionar ativos: {exc}"
        )
