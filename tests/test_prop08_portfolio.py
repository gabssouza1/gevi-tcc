"""Property test — Propriedade 8 (Integridade do Portfólio) — Tarefa 6.5.

**Propriedade 8: Integridade do Portfólio**

    Concentrações acima de 30% devem ser identificadas e reportadas.

    Verificação (design):
        ``(A.percentual > 30%) ⟹ A ∈ concentracoes_identificadas``

Este arquivo verifica a lógica pura de ``lambdas/fn-analise-portfolio/handler.py``
(``analisar_concentracoes`` e ``analisar_portfolio``): dado um portfólio
qualquer, TODO ativo com ``percentual > LIMIAR_CONCENTRACAO_PCT`` (30%) deve
aparecer nas concentrações reportadas e NENHUM ativo com ``percentual <= 30``
pode aparecer. Cobre também a borda ``percentual == 30`` — que NÃO é
concentração, pois o limiar usa comparação estrita ``>``.

**Validates: Requisito 9.2**
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from shared import (
    LIMIAR_CONCENTRACAO_PCT,
    AtivoPortfolio,
    Portfolio,
    Tipos_Ativo,
)

# ---------------------------------------------------------------------------
# Importação do handler de ``lambdas/fn-analise-portfolio/handler.py``.
# A pasta usa hífens (não é um pacote Python importável por nome), então
# carregamos o módulo diretamente pelo caminho de arquivo via importlib.
# O ``from shared import ...`` interno funciona porque ``shared`` está no
# ambiente de testes.
# ---------------------------------------------------------------------------
_RAIZ = Path(__file__).resolve().parents[1]
_HANDLER_PATH = _RAIZ / "lambdas" / "fn-analise-portfolio" / "handler.py"

_spec = importlib.util.spec_from_file_location(
    "fn_analise_portfolio_handler", _HANDLER_PATH
)
assert _spec is not None and _spec.loader is not None
handler_mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = handler_mod
_spec.loader.exec_module(handler_mod)


# ---------------------------------------------------------------------------
# Estratégias (geradores) — portfólios aleatórios
# ---------------------------------------------------------------------------
# Percentuais no intervalo válido [0, 100]. Inclui valores em torno da borda de
# 30 para exercitar a comparação estrita (30 não concentra; 30.01 concentra).
_PERCENTUAIS = st.floats(
    min_value=0.0, max_value=100.0, allow_nan=False, allow_infinity=False
)


@st.composite
def _ativos(draw: st.DrawFn) -> AtivoPortfolio:
    """Gera um ``AtivoPortfolio`` com percentual em [0, 100]."""
    indice = draw(st.integers(min_value=0, max_value=9999))
    return AtivoPortfolio(
        user_id="user-teste",
        asset_id=f"ATIVO-{indice}",
        tipo_ativo=draw(st.sampled_from(list(Tipos_Ativo))),
        percentual=draw(_PERCENTUAIS),
        valor=draw(
            st.floats(
                min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False
            )
        ),
    )


@st.composite
def _portfolios(draw: st.DrawFn) -> Portfolio:
    """Gera um ``Portfolio`` com de 0 a 8 ativos e ``asset_id`` únicos."""
    ativos = draw(
        st.lists(_ativos(), min_size=0, max_size=8, unique_by=lambda a: a.asset_id)
    )
    return Portfolio(user_id="user-teste", ativos=ativos)


# ---------------------------------------------------------------------------
# Propriedade 8: (A.percentual > 30%) ⟺ A ∈ concentracoes_identificadas
# ---------------------------------------------------------------------------
@pytest.mark.property
@given(portfolio=_portfolios())
def test_concentracoes_reportam_exatamente_acima_do_limiar(
    portfolio: Portfolio,
) -> None:
    """Todo ativo > 30% aparece; nenhum <= 30% aparece (Propriedade 8).

    **Validates: Requisito 9.2**
    """
    concentracoes = handler_mod.analisar_concentracoes(portfolio)
    ids_reportados = {c["asset_id"] for c in concentracoes}

    esperados_acima = {
        a.asset_id for a in portfolio.ativos if a.percentual > LIMIAR_CONCENTRACAO_PCT
    }
    proibidos_ate_limiar = {
        a.asset_id for a in portfolio.ativos if a.percentual <= LIMIAR_CONCENTRACAO_PCT
    }

    # (⟹) Todo ativo com percentual > 30 deve ser reportado.
    assert esperados_acima <= ids_reportados
    # (⟸) Nenhum ativo com percentual <= 30 pode ser reportado.
    assert ids_reportados.isdisjoint(proibidos_ate_limiar)
    # Equivalência exata: o conjunto reportado é exatamente o esperado.
    assert ids_reportados == esperados_acima

    # Cada concentração reportada de fato excede o limiar estrito.
    for concentracao in concentracoes:
        assert concentracao["percentual"] > LIMIAR_CONCENTRACAO_PCT


@pytest.mark.property
@given(portfolio=_portfolios())
def test_analisar_portfolio_coerente_com_concentracoes(portfolio: Portfolio) -> None:
    """A resposta consolidada de ``analisar_portfolio`` reflete a Propriedade 8.

    ``concentracoes`` == ``analisar_concentracoes`` e o flag
    ``possui_concentracao_excessiva`` é verdadeiro sse e somente se existe algum
    ativo acima do limiar.

    **Validates: Requisito 9.2**
    """
    analise = handler_mod.analisar_portfolio(portfolio)
    ids_reportados = {c["asset_id"] for c in analise["concentracoes"]}
    esperados_acima = {
        a.asset_id for a in portfolio.ativos if a.percentual > LIMIAR_CONCENTRACAO_PCT
    }

    assert ids_reportados == esperados_acima
    assert analise["possui_concentracao_excessiva"] == bool(esperados_acima)
    assert analise["limiar_concentracao_pct"] == LIMIAR_CONCENTRACAO_PCT


# ---------------------------------------------------------------------------
# Borda explícita: percentual == 30 NÃO é concentração (comparação estrita ">")
# ---------------------------------------------------------------------------
@pytest.mark.property
@given(
    # Um valor estritamente acima de 30 (concentra) e um valor <= 30 (não concentra).
    acima=st.floats(
        min_value=30.0, max_value=100.0, allow_nan=False, allow_infinity=False
    ).filter(lambda x: x > LIMIAR_CONCENTRACAO_PCT),
    ate_limiar=st.floats(
        min_value=0.0, max_value=30.0, allow_nan=False, allow_infinity=False
    ),
)
def test_borda_do_limiar_estrito(acima: float, ate_limiar: float) -> None:
    """Confirma a borda ``==30`` (não concentra) vs ``>30`` (concentra).

    **Validates: Requisito 9.2**
    """
    portfolio = Portfolio(
        user_id="user-teste",
        ativos=[
            AtivoPortfolio(
                user_id="user-teste",
                asset_id="ACIMA",
                tipo_ativo=Tipos_Ativo.RENDA_VARIAVEL,
                percentual=acima,
                valor=100.0,
            ),
            AtivoPortfolio(
                user_id="user-teste",
                asset_id="ATE-LIMIAR",
                tipo_ativo=Tipos_Ativo.RENDA_FIXA,
                percentual=ate_limiar,
                valor=100.0,
            ),
        ],
    )

    ids_reportados = {c["asset_id"] for c in handler_mod.analisar_concentracoes(portfolio)}
    assert "ACIMA" in ids_reportados
    assert "ATE-LIMIAR" not in ids_reportados


def test_percentual_exatamente_30_nao_e_concentracao() -> None:
    """Caso unitário explícito: ``percentual == 30`` não é reportado.

    **Validates: Requisito 9.2**
    """
    portfolio = Portfolio(
        user_id="user-teste",
        ativos=[
            AtivoPortfolio(
                user_id="user-teste",
                asset_id="EXATO-30",
                tipo_ativo=Tipos_Ativo.FII,
                percentual=LIMIAR_CONCENTRACAO_PCT,  # 30.0
                valor=100.0,
            )
        ],
    )

    assert handler_mod.analisar_concentracoes(portfolio) == []
