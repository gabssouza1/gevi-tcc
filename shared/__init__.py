"""Pacote de código comum reutilizado pelas funções Lambda.

Reexporta as enumerações de domínio e os modelos de dados para permitir
importações diretas a partir de ``shared`` (ex.: ``from shared import Perfil_Risco``),
mantendo baixo acoplamento entre as Lambdas e o código compartilhado
(Requisito 13.2).
"""

from __future__ import annotations

from .enums import (
    Nivel_Conhecimento,
    Nivel_Risco,
    Perfil_Risco,
    Tipo_Cenario,
    Tipos_Ativo,
)
from .models import (
    DISCLAIMER_SIMULACAO,
    LIMIAR_CONCENTRACAO_PCT,
    AnaliseRisco,
    AtivoPortfolio,
    AtivoRecomendado,
    Cenario,
    Indicador,
    LimiarAlerta,
    Perfil_Investidor,
    Portfolio,
    Recomendacao,
    Simulacao,
)
from .historico import (
    LIMITE_CONSULTA_PADRAO,
    TIPO_RECOMENDACAO,
    TIPO_SIMULACAO,
    consultar_historico,
    gravar_recomendacao,
    montar_item_recomendacao,
)
from .logs import log_erro, log_evento, obter_logger
from .serialization import from_dict, to_dict
from .validation import (
    ErroValidacao,
    identificar_concentracoes,
    validar_ativo_portfolio,
    validar_email,
    validar_indicador,
    validar_perfil_investidor,
    validar_portfolio,
    validar_recomendacao,
    validar_simulacao,
)

__all__ = [
    # enums
    "Nivel_Conhecimento",
    "Nivel_Risco",
    "Perfil_Risco",
    "Tipo_Cenario",
    "Tipos_Ativo",
    # models
    "DISCLAIMER_SIMULACAO",
    "LIMIAR_CONCENTRACAO_PCT",
    "AnaliseRisco",
    "AtivoPortfolio",
    "AtivoRecomendado",
    "Cenario",
    "Indicador",
    "LimiarAlerta",
    "Perfil_Investidor",
    "Portfolio",
    "Recomendacao",
    "Simulacao",
    # histórico (recomendações e simulações)
    "LIMITE_CONSULTA_PADRAO",
    "TIPO_RECOMENDACAO",
    "TIPO_SIMULACAO",
    "consultar_historico",
    "gravar_recomendacao",
    "montar_item_recomendacao",
    # logging estruturado
    "obter_logger",
    "log_evento",
    "log_erro",
    # serialização
    "to_dict",
    "from_dict",
    # validação
    "ErroValidacao",
    "identificar_concentracoes",
    "validar_ativo_portfolio",
    "validar_email",
    "validar_indicador",
    "validar_perfil_investidor",
    "validar_portfolio",
    "validar_recomendacao",
    "validar_simulacao",
]
