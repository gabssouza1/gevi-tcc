"""Modelos de dados (dataclasses) compartilhados pelas Lambdas.

Mapeiam as entidades do diagrama de classes do TCC para a implementação física:

- ``Perfil_Investidor`` → DynamoDB ``Users``
- ``Indicador``         → DynamoDB ``EconomicIndicators``
- ``AtivoPortfolio`` / ``Portfolio`` → DynamoDB ``Portfolios``
- ``Recomendacao``      → DynamoDB ``Historico``
- ``Simulacao``         → DynamoDB ``Historico``
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .enums import (
    Nivel_Conhecimento,
    Nivel_Risco,
    Perfil_Risco,
    Tipo_Cenario,
    Tipos_Ativo,
)

# Disclaimer obrigatório anexado a toda simulação (Requisito 6.3).
DISCLAIMER_SIMULACAO = (
    "Projeções baseadas em dados históricos e não garantem resultados futuros."
)

# Limiar de concentração de portfólio que deve ser reportado (Requisito 9.2).
LIMIAR_CONCENTRACAO_PCT = 30.0


@dataclass
class LimiarAlerta:
    """Limiar de variação configurado pelo usuário para disparo de alerta."""

    indicator_id: str
    limiar_percentual: float


@dataclass
class Perfil_Investidor:
    """Perfil do investidor persistido na tabela ``Users``."""

    user_id: str
    email: str
    nome: str
    nivel_conhecimento: Nivel_Conhecimento
    tolerancia_risco: Perfil_Risco
    objetivos: list[str] = field(default_factory=list)
    limiares_alerta: list[LimiarAlerta] = field(default_factory=list)


@dataclass
class Indicador:
    """Indicador macroeconômico persistido em ``EconomicIndicators``."""

    indicator_id: str
    date: str  # ISO-8601 (sort key)
    valor: float
    valor_anterior: float | None = None
    variacao_percentual: float | None = None
    # Unidade do ``valor`` original da fonte: "a.a." (ao ano), "a.m." (ao mês),
    # "a.d." (ao dia) ou "preco". Permite interpretar/exibir corretamente.
    unidade: str = ""
    # Taxa efetiva anualizada (% a.a.), base canônica para simulações e exibição.
    # ``None`` para indicadores que não são taxa (ex.: dólar/preço).
    valor_anualizado: float | None = None


@dataclass
class AtivoPortfolio:
    """Um ativo componente do portfólio (item de ``Portfolios``)."""

    user_id: str
    asset_id: str
    tipo_ativo: Tipos_Ativo
    percentual: float  # participação no portfólio (0–100)
    valor: float


@dataclass
class Portfolio:
    """Composição completa do portfólio de um usuário."""

    user_id: str
    ativos: list[AtivoPortfolio] = field(default_factory=list)


@dataclass
class AnaliseRisco:
    """Análise de risco associada a uma recomendação (Requisito 4)."""

    classificacao: Nivel_Risco
    volatilidade: float
    exposicao: float


@dataclass
class AtivoRecomendado:
    """Ativo sugerido dentro de uma recomendação."""

    asset_id: str
    tipo_ativo: Tipos_Ativo
    nivel_risco: Nivel_Risco
    rentabilidade_esperada: float
    prazo: str


@dataclass
class Recomendacao:
    """Recomendação consolidada persistida no ``Historico`` (PK userId/SK timestamp)."""

    user_id: str
    timestamp: str
    ativos: list[AtivoRecomendado]
    nivel_risco: Nivel_Risco
    analise_risco: AnaliseRisco
    explicacao: str
    conteudo_educativo: str | None = None


@dataclass
class Cenario:
    """Projeção de um cenário de simulação."""

    tipo: Tipo_Cenario
    valor_projetado: float


@dataclass
class Simulacao:
    """Simulação de investimento persistida no ``Historico``."""

    user_id: str
    timestamp: str
    valor_inicial: float
    prazo_meses: int
    tipo_ativo: Tipos_Ativo
    cenario_pessimista: float
    cenario_realista: float
    cenario_otimista: float
    disclaimer: str = DISCLAIMER_SIMULACAO
