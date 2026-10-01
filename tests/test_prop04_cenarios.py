"""Property test — Propriedade 4: Consistência de Cenários de Simulação — Tarefa 6.3.

**Propriedade 4: Consistência de Cenários de Simulação**

    Os três cenários projetados devem manter a ordem:

        pessimista ≤ realista ≤ otimista

    Verificação (design): ``S.pessimista <= S.realista <= S.otimista``.

Este arquivo cobre a função pura ``calcular_cenarios`` de
``lambdas/fn-calculo-simulacao/handler.py`` e também o orquestrador
``analisar_simulacao`` (que monta e valida a ``Simulacao``). A ordem deve valer
para quaisquer parâmetros válidos, inclusive **após o arredondamento** para 2
casas decimais (que é monotônico e, portanto, preserva a ordem).

Casos especiais exercitados explicitamente:
  - volatilidade ``0`` → os três cenários são iguais (desvio nulo);
  - prazos longos (dezenas de anos) → o horizonte não quebra a ordem.

**Validates: Requisito 6.2**
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Importação do handler de ``lambdas/fn-calculo-simulacao/handler.py``.
# A pasta usa hífens (não é um pacote Python importável por nome), então
# carregamos o módulo diretamente pelo caminho de arquivo via importlib,
# registrando-o em ``sys.modules`` ANTES de ``exec_module`` (o ``from shared
# import ...`` interno funciona porque ``shared`` está instalado no ambiente).
# ---------------------------------------------------------------------------
_RAIZ = Path(__file__).resolve().parents[1]
_HANDLER_PATH = _RAIZ / "lambdas" / "fn-calculo-simulacao" / "handler.py"

_spec = importlib.util.spec_from_file_location(
    "fn_calculo_simulacao_handler", _HANDLER_PATH
)
assert _spec is not None and _spec.loader is not None
handler_mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = handler_mod
_spec.loader.exec_module(handler_mod)

# Reexporta o enum para montar os indicadores/tipo de ativo do orquestrador.
Tipos_Ativo = handler_mod.Tipos_Ativo


# ---------------------------------------------------------------------------
# Estratégias — restringem o espaço de entrada a parâmetros VÁLIDOS.
# Limites amplos, mas finitos, para evitar overflow do gerador (não do código):
#   - valor_inicial >= 0
#   - prazo_meses > 0 (inclui prazos longos: até 600 meses = 50 anos)
#   - taxa_anual variada (pode ser negativa)
#   - volatilidade >= 0 (inclui 0)
# ---------------------------------------------------------------------------
_valor_inicial = st.floats(
    min_value=0.0, max_value=1e7, allow_nan=False, allow_infinity=False
)
_prazo_meses = st.integers(min_value=1, max_value=600)
_taxa_anual = st.floats(
    min_value=-50.0, max_value=100.0, allow_nan=False, allow_infinity=False
)
_volatilidade = st.floats(
    min_value=0.0, max_value=200.0, allow_nan=False, allow_infinity=False
)


@pytest.mark.property
@given(
    valor_inicial=_valor_inicial,
    prazo_meses=_prazo_meses,
    taxa_anual=_taxa_anual,
    volatilidade=_volatilidade,
)
@settings(deadline=None)
def test_cenarios_mantem_ordem(
    valor_inicial: float,
    prazo_meses: int,
    taxa_anual: float,
    volatilidade: float,
) -> None:
    """pessimista ≤ realista ≤ otimista para quaisquer parâmetros válidos.

    A ordem vale já com os valores arredondados devolvidos por
    ``calcular_cenarios`` (arredondamento monotônico preserva a ordem).

    **Validates: Requisito 6.2**
    """
    pessimista, realista, otimista = handler_mod.calcular_cenarios(
        valor_inicial, prazo_meses, taxa_anual, volatilidade
    )

    assert pessimista <= realista <= otimista
    # Nenhum cenário pode ser negativo (piso em zero no cálculo).
    assert pessimista >= 0.0


@pytest.mark.property
@given(
    valor_inicial=_valor_inicial,
    prazo_meses=_prazo_meses,
    taxa_anual=_taxa_anual,
)
@settings(deadline=None)
def test_volatilidade_zero_cenarios_iguais(
    valor_inicial: float,
    prazo_meses: int,
    taxa_anual: float,
) -> None:
    """Volatilidade ``0`` → os três cenários coincidem (desvio nulo).

    **Validates: Requisito 6.2**
    """
    pessimista, realista, otimista = handler_mod.calcular_cenarios(
        valor_inicial, prazo_meses, taxa_anual, 0.0
    )

    assert pessimista == realista == otimista


@pytest.mark.property
@given(
    valor_inicial=st.floats(
        min_value=1.0, max_value=1e6, allow_nan=False, allow_infinity=False
    ),
    # Foca em prazos longos: de 20 a 50 anos.
    prazo_meses=st.integers(min_value=240, max_value=600),
    taxa_anual=_taxa_anual,
    volatilidade=_volatilidade,
)
@settings(deadline=None)
def test_prazos_longos_mantem_ordem(
    valor_inicial: float,
    prazo_meses: int,
    taxa_anual: float,
    volatilidade: float,
) -> None:
    """Prazos longos (20–50 anos) não quebram a ordem dos cenários.

    **Validates: Requisito 6.2**
    """
    pessimista, realista, otimista = handler_mod.calcular_cenarios(
        valor_inicial, prazo_meses, taxa_anual, volatilidade
    )

    assert pessimista <= realista <= otimista


@pytest.mark.property
@given(
    valor_inicial=_valor_inicial,
    prazo_meses=_prazo_meses,
    tipo_ativo=st.sampled_from(list(Tipos_Ativo)),
    cdi=st.floats(min_value=0.0, max_value=30.0, allow_nan=False, allow_infinity=False),
    ipca=st.floats(min_value=0.0, max_value=30.0, allow_nan=False, allow_infinity=False),
)
@settings(deadline=None)
def test_analisar_simulacao_preserva_ordem(
    valor_inicial: float,
    prazo_meses: int,
    tipo_ativo,
    cdi: float,
    ipca: float,
) -> None:
    """O orquestrador ``analisar_simulacao`` devolve cenários ordenados.

    A ``Simulacao`` montada é validada internamente (``validar_simulacao``),
    de modo que a ordem pessimista ≤ realista ≤ otimista também vale ponta a
    ponta com dados de mercado válidos.

    **Validates: Requisito 6.2**
    """
    indicadores = {"CDI": cdi, "IPCA": ipca}

    simulacao, _analise = handler_mod.analisar_simulacao(
        user_id="u-teste",
        timestamp="2024-01-01T00:00:00+00:00",
        valor_inicial=valor_inicial,
        prazo_meses=prazo_meses,
        tipo_ativo=tipo_ativo,
        indicadores=indicadores,
    )

    assert (
        simulacao.cenario_pessimista
        <= simulacao.cenario_realista
        <= simulacao.cenario_otimista
    )
