"""Enumerações de domínio compartilhadas pelo backend.

Alinhadas ao diagrama de classes do TCC e ao documento de design. Cada enum tem
um domínio distinto para evitar ambiguidade entre "moderado", "médio" e
"realista":

- ``Perfil_Risco``   → tolerância a risco do investidor (usa ``MODERADO``)
- ``Nivel_Risco``    → risco de uma recomendação/ativo (usa ``MEDIO``)
- ``Tipo_Cenario``   → cenário de simulação (usa ``REALISTA``)
"""

from __future__ import annotations

from enum import Enum


class Tipos_Ativo(str, Enum):
    """Classes de ativo suportadas pelo Sistema (diagrama de classes do TCC)."""

    RENDA_FIXA = "RENDA_FIXA"
    RENDA_VARIAVEL = "RENDA_VARIAVEL"
    FII = "FII"
    CRIPTO = "CRIPTO"


class Perfil_Risco(str, Enum):
    """Tolerância a risco do investidor (persistida em ``Users``)."""

    CONSERVADOR = "CONSERVADOR"
    MODERADO = "MODERADO"
    ARROJADO = "ARROJADO"

    @property
    def ordinal(self) -> int:
        """Posição na escala de tolerância (0 = menor tolerância)."""
        return _ORDEM_PERFIL_RISCO.index(self)


class Nivel_Risco(str, Enum):
    """Classificação de risco de uma recomendação ou ativo."""

    BAIXO = "BAIXO"
    MEDIO = "MEDIO"
    ALTO = "ALTO"

    @property
    def ordinal(self) -> int:
        """Posição na escala de risco (0 = menor risco)."""
        return _ORDEM_NIVEL_RISCO.index(self)


class Tipo_Cenario(str, Enum):
    """Cenários de simulação de investimento."""

    OTIMISTA = "OTIMISTA"
    REALISTA = "REALISTA"
    PESSIMISTA = "PESSIMISTA"


class Nivel_Conhecimento(str, Enum):
    """Nível de conhecimento financeiro do usuário (adaptação de linguagem)."""

    BASICO = "basico"
    AVANCADO = "avancado"


# Ordens de escala usadas pelas propriedades ``ordinal`` acima. Mantidas em nível
# de módulo para não serem interpretadas como membros dos enums.
_ORDEM_PERFIL_RISCO = [Perfil_Risco.CONSERVADOR, Perfil_Risco.MODERADO, Perfil_Risco.ARROJADO]
_ORDEM_NIVEL_RISCO = [Nivel_Risco.BAIXO, Nivel_Risco.MEDIO, Nivel_Risco.ALTO]
