"""Funções de validação reutilizadas pelas Lambdas.

Centralizam a checagem de consistência dos modelos de domínio antes de
persistir ou apresentar dados financeiros (Requisito 14.3). Cada função lança
``ErroValidacao`` com uma mensagem específica por campo inválido, permitindo que
as Lambdas devolvam erros claros ao chamador (ex.: Requisitos 1.2, 1.4).

As validações são propositalmente independentes de AWS: operam apenas sobre as
dataclasses de ``shared.models`` e as enumerações de ``shared.enums``.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any

from .enums import Nivel_Conhecimento, Nivel_Risco, Perfil_Risco, Tipos_Ativo
from .models import (
    LIMIAR_CONCENTRACAO_PCT,
    AtivoPortfolio,
    Indicador,
    Perfil_Investidor,
    Portfolio,
    Recomendacao,
    Simulacao,
)

# Regex simples de e-mail (formato ``local@dominio.tld``), suficiente para
# validação de entrada — a verificação definitiva é feita pelo Cognito.
_REGEX_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ErroValidacao(ValueError):
    """Erro de validação de um modelo de domínio compartilhado."""


# ---------------------------------------------------------------------------
# Helpers de validação de campos
# ---------------------------------------------------------------------------
def _exigir_texto(valor: Any, campo: str) -> str:
    """Garante que ``valor`` é uma string não vazia."""
    if not isinstance(valor, str) or not valor.strip():
        raise ErroValidacao(f"O campo '{campo}' é obrigatório e deve ser texto não vazio.")
    return valor


def _exigir_numero(valor: Any, campo: str) -> float:
    """Garante que ``valor`` é numérico (int/float, exceto bool)."""
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        raise ErroValidacao(f"O campo '{campo}' deve ser numérico.")
    return float(valor)


def _exigir_nao_negativo(valor: Any, campo: str) -> float:
    """Garante que ``valor`` é numérico e maior ou igual a zero."""
    numero = _exigir_numero(valor, campo)
    if numero < 0:
        raise ErroValidacao(f"O campo '{campo}' não pode ser negativo.")
    return numero


def _exigir_percentual(valor: Any, campo: str) -> float:
    """Garante que ``valor`` é um percentual no intervalo [0, 100]."""
    numero = _exigir_numero(valor, campo)
    if not 0 <= numero <= 100:
        raise ErroValidacao(f"O campo '{campo}' deve estar entre 0 e 100.")
    return numero


def _exigir_enum(valor: Any, enum_cls: type[Enum], campo: str) -> Enum:
    """Garante que ``valor`` é um membro do enum informado."""
    if isinstance(valor, enum_cls):
        return valor
    try:
        return enum_cls(valor)
    except ValueError as exc:
        validos = ", ".join(membro.value for membro in enum_cls)
        raise ErroValidacao(
            f"O campo '{campo}' deve ser um de: {validos}."
        ) from exc


def validar_email(valor: Any, campo: str = "email") -> str:
    """Valida o formato básico de um e-mail."""
    texto = _exigir_texto(valor, campo)
    if not _REGEX_EMAIL.match(texto):
        raise ErroValidacao(f"O campo '{campo}' não contém um e-mail válido.")
    return texto


# ---------------------------------------------------------------------------
# Validação dos modelos de domínio
# ---------------------------------------------------------------------------
def validar_perfil_investidor(perfil: Perfil_Investidor) -> Perfil_Investidor:
    """Valida o Perfil_Investidor (Requisitos 1.2, 1.4).

    Garante identificação, e-mail válido e a presença do nível de conhecimento
    e da tolerância a risco coletados no cadastro.
    """
    _exigir_texto(perfil.user_id, "user_id")
    validar_email(perfil.email)
    _exigir_texto(perfil.nome, "nome")
    _exigir_enum(perfil.nivel_conhecimento, Nivel_Conhecimento, "nivel_conhecimento")
    _exigir_enum(perfil.tolerancia_risco, Perfil_Risco, "tolerancia_risco")
    if not isinstance(perfil.objetivos, list):
        raise ErroValidacao("O campo 'objetivos' deve ser uma lista.")
    return perfil


def validar_indicador(indicador: Indicador) -> Indicador:
    """Valida um Indicador macroeconômico (Requisito 14.3)."""
    _exigir_texto(indicador.indicator_id, "indicator_id")
    _exigir_texto(indicador.date, "date")
    _exigir_numero(indicador.valor, "valor")
    if indicador.valor_anterior is not None:
        _exigir_numero(indicador.valor_anterior, "valor_anterior")
    if indicador.variacao_percentual is not None:
        _exigir_numero(indicador.variacao_percentual, "variacao_percentual")
    return indicador


def validar_ativo_portfolio(ativo: AtivoPortfolio) -> AtivoPortfolio:
    """Valida um ativo componente do portfólio (Requisitos 3.2, 9.1)."""
    _exigir_texto(ativo.user_id, "user_id")
    _exigir_texto(ativo.asset_id, "asset_id")
    _exigir_enum(ativo.tipo_ativo, Tipos_Ativo, "tipo_ativo")
    _exigir_percentual(ativo.percentual, "percentual")
    _exigir_nao_negativo(ativo.valor, "valor")
    return ativo


def validar_portfolio(portfolio: Portfolio, *, tolerancia: float = 0.01) -> Portfolio:
    """Valida a composição do portfólio (Requisito 9.1).

    Além de validar cada ativo, verifica que a soma dos percentuais não excede
    100% (dentro de uma pequena tolerância para arredondamentos).
    """
    _exigir_texto(portfolio.user_id, "user_id")
    if not isinstance(portfolio.ativos, list):
        raise ErroValidacao("O campo 'ativos' deve ser uma lista.")
    for ativo in portfolio.ativos:
        validar_ativo_portfolio(ativo)
    soma = sum(ativo.percentual for ativo in portfolio.ativos)
    if soma > 100 + tolerancia:
        raise ErroValidacao(
            f"A soma dos percentuais do portfólio ({soma:.2f}%) excede 100%."
        )
    return portfolio


def identificar_concentracoes(portfolio: Portfolio) -> list[AtivoPortfolio]:
    """Retorna os ativos com concentração acima do limiar (Requisito 9.2).

    Corresponde à Propriedade 8 (Integridade do Portfólio): concentrações acima
    de ``LIMIAR_CONCENTRACAO_PCT`` (30%) devem ser identificadas e reportadas.
    """
    return [
        ativo for ativo in portfolio.ativos if ativo.percentual > LIMIAR_CONCENTRACAO_PCT
    ]


def validar_recomendacao(recomendacao: Recomendacao) -> Recomendacao:
    """Valida uma Recomendacao (Requisitos 3.4, 4.1).

    Garante a presença obrigatória de análise de risco (Propriedade 2) e que
    cada ativo recomendado traga rentabilidade esperada e prazo (Requisito 3.4).
    """
    _exigir_texto(recomendacao.user_id, "user_id")
    _exigir_texto(recomendacao.timestamp, "timestamp")
    _exigir_enum(recomendacao.nivel_risco, Nivel_Risco, "nivel_risco")
    _exigir_texto(recomendacao.explicacao, "explicacao")

    if recomendacao.analise_risco is None:
        raise ErroValidacao("Toda recomendação deve conter análise de risco.")
    _exigir_enum(
        recomendacao.analise_risco.classificacao, Nivel_Risco, "analise_risco.classificacao"
    )
    _exigir_nao_negativo(recomendacao.analise_risco.volatilidade, "analise_risco.volatilidade")
    _exigir_nao_negativo(recomendacao.analise_risco.exposicao, "analise_risco.exposicao")

    if not isinstance(recomendacao.ativos, list) or not recomendacao.ativos:
        raise ErroValidacao("A recomendação deve conter ao menos um ativo sugerido.")
    for ativo in recomendacao.ativos:
        _exigir_texto(ativo.asset_id, "ativos[].asset_id")
        _exigir_enum(ativo.tipo_ativo, Tipos_Ativo, "ativos[].tipo_ativo")
        _exigir_enum(ativo.nivel_risco, Nivel_Risco, "ativos[].nivel_risco")
        _exigir_numero(ativo.rentabilidade_esperada, "ativos[].rentabilidade_esperada")
        _exigir_texto(ativo.prazo, "ativos[].prazo")
    return recomendacao


def validar_simulacao(simulacao: Simulacao) -> Simulacao:
    """Valida uma Simulacao (Requisitos 6.1, 6.2, 6.3).

    Verifica os parâmetros de entrada, a ordem dos cenários
    (pessimista ≤ realista ≤ otimista, Propriedade 4) e a presença do
    disclaimer obrigatório.
    """
    _exigir_texto(simulacao.user_id, "user_id")
    _exigir_texto(simulacao.timestamp, "timestamp")
    _exigir_nao_negativo(simulacao.valor_inicial, "valor_inicial")
    _exigir_enum(simulacao.tipo_ativo, Tipos_Ativo, "tipo_ativo")

    if not isinstance(simulacao.prazo_meses, int) or isinstance(simulacao.prazo_meses, bool):
        raise ErroValidacao("O campo 'prazo_meses' deve ser um inteiro.")
    if simulacao.prazo_meses <= 0:
        raise ErroValidacao("O campo 'prazo_meses' deve ser maior que zero.")

    _exigir_numero(simulacao.cenario_pessimista, "cenario_pessimista")
    _exigir_numero(simulacao.cenario_realista, "cenario_realista")
    _exigir_numero(simulacao.cenario_otimista, "cenario_otimista")
    if not (
        simulacao.cenario_pessimista
        <= simulacao.cenario_realista
        <= simulacao.cenario_otimista
    ):
        raise ErroValidacao(
            "Os cenários devem respeitar a ordem: pessimista ≤ realista ≤ otimista."
        )

    _exigir_texto(simulacao.disclaimer, "disclaimer")
    return simulacao
