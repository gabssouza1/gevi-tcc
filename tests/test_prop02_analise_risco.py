"""Property test — Propriedade 2 — Tarefa 6.2.

**Propriedade 2: Presença Obrigatória de Análise de Risco**

    Toda recomendação/simulação gerada deve conter análise de risco associada.

    Verificação (design):
        ``R.analiseRisco != null && R.analiseRisco.classificacao in
        ['baixo', 'medio', 'alto']``

Este arquivo cobre o cálculo puro de ``fn-calculo-simulacao`` (acionada pelo
Agente_Risco): para qualquer simulação válida com dados de mercado disponíveis
(CDI/SELIC), ``analisar_simulacao`` sempre retorna uma :class:`AnaliseRisco`
cuja ``classificacao`` pertence a ``Nivel_Risco`` (BAIXO/MEDIO/ALTO) e cujas
``volatilidade`` e ``exposicao`` são não negativas (Requisito 4.1 — volatilidade
e exposição; Requisito 4.2 — classificação compreensível).

A pasta ``lambdas/fn-calculo-simulacao`` usa hífen no nome (não é um pacote
Python importável por nome), então o ``handler.py`` é carregado diretamente pelo
caminho de arquivo via ``importlib`` (registrado em ``sys.modules`` antes do
``exec_module`` para que dataclasses/anotações resolvam corretamente). O
``from shared import ...`` interno funciona porque ``shared`` está disponível no
ambiente de testes.

**Validates: Requisitos 4.1, 4.2**
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from shared import AnaliseRisco, Nivel_Risco, Tipos_Ativo

# ---------------------------------------------------------------------------
# Importação do handler de ``lambdas/fn-calculo-simulacao/handler.py``.
# ---------------------------------------------------------------------------
_RAIZ = Path(__file__).resolve().parents[1]
_HANDLER_PATH = _RAIZ / "lambdas" / "fn-calculo-simulacao" / "handler.py"

_spec = importlib.util.spec_from_file_location(
    "fn_calculo_simulacao_handler", _HANDLER_PATH
)
assert _spec is not None and _spec.loader is not None
handler_mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = handler_mod  # registra antes de exec_module
_spec.loader.exec_module(handler_mod)


# ---------------------------------------------------------------------------
# Estratégias: parâmetros válidos de simulação e indicadores com CDI/SELIC.
# ---------------------------------------------------------------------------
_valor_inicial = st.floats(
    min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False
)
_prazo_meses = st.integers(min_value=1, max_value=600)
_tipo_ativo = st.sampled_from(list(Tipos_Ativo))

# Taxas de referência (percentuais anuais) realistas; ao menos uma entre
# CDI/SELIC precisa existir para que os dados de mercado estejam disponíveis.
_taxa = st.floats(min_value=0.0, max_value=50.0, allow_nan=False, allow_infinity=False)
# IPCA pode estar ausente ou negativo (deflação); o cálculo deve permanecer robusto.
_ipca = st.one_of(
    st.none(),
    st.floats(min_value=-10.0, max_value=30.0, allow_nan=False, allow_infinity=False),
)


@st.composite
def _indicadores(draw: st.DrawFn) -> dict[str, float]:
    """Gera indicadores garantindo CDI e/ou SELIC disponíveis (Requisito 4.4)."""
    indicadores: dict[str, float] = {}
    incluir_cdi = draw(st.booleans())
    incluir_selic = draw(st.booleans())
    # Garante que ao menos uma taxa de referência exista.
    if not (incluir_cdi or incluir_selic):
        incluir_cdi = True
    if incluir_cdi:
        indicadores["CDI"] = draw(_taxa)
    if incluir_selic:
        indicadores["SELIC"] = draw(_taxa)
    ipca = draw(_ipca)
    if ipca is not None:
        indicadores["IPCA"] = ipca
    return indicadores


@pytest.mark.property
@given(
    valor_inicial=_valor_inicial,
    prazo_meses=_prazo_meses,
    tipo_ativo=_tipo_ativo,
    indicadores=_indicadores(),
)
@settings(deadline=None)
def test_simulacao_sempre_tem_analise_de_risco(
    valor_inicial: float,
    prazo_meses: int,
    tipo_ativo: Tipos_Ativo,
    indicadores: dict[str, float],
) -> None:
    """``analisar_simulacao`` sempre produz uma ``AnaliseRisco`` válida.

    A classificação pertence a ``Nivel_Risco`` (BAIXO/MEDIO/ALTO) e tanto a
    volatilidade quanto a exposição são não negativas.

    **Validates: Requisitos 4.1, 4.2**
    """
    _simulacao, analise = handler_mod.analisar_simulacao(
        user_id="user-teste",
        timestamp="2024-01-01T00:00:00+00:00",
        valor_inicial=valor_inicial,
        prazo_meses=prazo_meses,
        tipo_ativo=tipo_ativo,
        indicadores=indicadores,
    )

    # Presença obrigatória da análise de risco (Propriedade 2).
    assert analise is not None
    assert isinstance(analise, AnaliseRisco)

    # Classificação compreensível ∈ Nivel_Risco (Requisito 4.2).
    assert isinstance(analise.classificacao, Nivel_Risco)
    assert analise.classificacao in (
        Nivel_Risco.BAIXO,
        Nivel_Risco.MEDIO,
        Nivel_Risco.ALTO,
    )

    # Volatilidade e exposição não negativas (Requisito 4.1).
    assert analise.volatilidade >= 0
    assert analise.exposicao >= 0
