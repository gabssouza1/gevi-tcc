"""Property test da Propriedade 9: Fallback com Cache em Indisponibilidade.

**Valida: Requisito 14.2**

Propriedade 9 (design):
    Quando a API externa está indisponível, o sistema deve usar os dados em
    cache e informar a data da última atualização.

    Verificação formal:
        servico_indisponivel(E) ⟹
            resposta.dados == cache(E) && resposta.dataAtualizacao == cache(E).timestamp

Este módulo exercita a função pura ``resolver_com_fallback`` de
``lambdas/fn-consulta-APIs/handler.py`` (carregada via ``importlib`` por conta
do hífen no nome da pasta) cobrindo os três cenários possíveis:

- **API disponível** (``indicador_novo`` presente) → ``origem == "api"`` e a
  ``data_atualizacao`` é o ``date`` do dado novo.
- **API indisponível com cache** (``indicador_novo is None`` e o ``indicator_id``
  está no cache) → ``origem == "cache"`` e a ``data_atualizacao`` é o ``date``
  (último timestamp) do valor em cache — o coração da Propriedade 9 / Req 14.2.
- **API indisponível sem cache** (``indicador_novo is None`` e sem cache) →
  ``origem == "indisponivel"`` e ``data_atualizacao is None``.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from shared import Indicador

# ---------------------------------------------------------------------------
# Carregamento do módulo handler (pasta com hífen → não é importável direto)
# ---------------------------------------------------------------------------
_HANDLER_PATH = (
    Path(__file__).resolve().parents[1]
    / "lambdas"
    / "fn-consulta-APIs"
    / "handler.py"
)
_spec = importlib.util.spec_from_file_location("fn_consulta_apis_handler", _HANDLER_PATH)
assert _spec is not None and _spec.loader is not None
_handler = importlib.util.module_from_spec(_spec)
# Registra o módulo em sys.modules antes de executá-lo: no Python 3.14 o
# ``@dataclass`` resolve as anotações consultando ``sys.modules[__module__]``.
sys.modules[_spec.name] = _handler
_spec.loader.exec_module(_handler)

resolver_com_fallback = _handler.resolver_com_fallback
ResultadoIndicador = _handler.ResultadoIndicador


# ---------------------------------------------------------------------------
# Geradores (Hypothesis)
# ---------------------------------------------------------------------------
# Conjunto pequeno e fixo de IDs para que o cache colida com o ID consultado em
# uma fração relevante dos exemplos, exercitando de fato o ramo "cache".
_IDS = ["SELIC", "IPCA", "CDI", "DOLAR", "PETR4"]

_timestamps = st.integers(min_value=2000, max_value=2100).map(
    lambda ano: f"{ano:04d}-01-15T12:00:00+00:00"
)


@st.composite
def _indicadores(draw, indicator_id: str) -> Indicador:
    """Gera um ``Indicador`` válido para um dado ``indicator_id``."""
    return Indicador(
        indicator_id=indicator_id,
        date=draw(_timestamps),
        valor=draw(st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False)),
        valor_anterior=draw(
            st.one_of(
                st.none(),
                st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
            )
        ),
        variacao_percentual=draw(
            st.one_of(
                st.none(),
                st.floats(min_value=-1e3, max_value=1e3, allow_nan=False, allow_infinity=False),
            )
        ),
    )


@st.composite
def _cenarios(draw):
    """Gera um cenário aleatório (indicator_id, indicador_novo, cache)."""
    indicator_id = draw(st.sampled_from(_IDS))

    # A API pode ter respondido (dado novo) ou estar indisponível (None).
    indicador_novo = draw(
        st.one_of(st.none(), _indicadores(indicator_id))
    )

    # Cache com um subconjunto arbitrário de IDs (pode ou não conter o consultado).
    ids_em_cache = draw(st.lists(st.sampled_from(_IDS), unique=True, max_size=len(_IDS)))
    cache = {cid: draw(_indicadores(cid)) for cid in ids_em_cache}

    return indicator_id, indicador_novo, cache


# ---------------------------------------------------------------------------
# Propriedade 9
# ---------------------------------------------------------------------------
@pytest.mark.property
@given(cenario=_cenarios())
def test_prop09_fallback_com_cache(cenario) -> None:
    """Propriedade 9: fallback com cache em indisponibilidade (Req 14.2).

    Verifica os três ramos de ``resolver_com_fallback`` sobre cenários
    aleatórios, com ênfase no invariante da Propriedade 9: quando a API está
    indisponível e há cache, a resposta reusa o valor em cache e informa o
    ``date`` (último timestamp) da última atualização.
    """
    indicator_id, indicador_novo, cache = cenario

    resultado = resolver_com_fallback(indicator_id, indicador_novo, cache)

    # O resultado sempre preserva o indicator_id consultado.
    assert isinstance(resultado, ResultadoIndicador)
    assert resultado.indicator_id == indicator_id

    if indicador_novo is not None:
        # API disponível: usa o dado novo.
        assert resultado.origem == "api"
        assert resultado.indicador is indicador_novo
        assert resultado.data_atualizacao == indicador_novo.date
    elif indicator_id in cache:
        # API indisponível COM cache (Propriedade 9 / Req 14.2):
        # reusa o valor em cache e informa a data da última atualização.
        assert resultado.origem == "cache"
        assert resultado.indicador is cache[indicator_id]
        assert resultado.data_atualizacao == cache[indicator_id].date
    else:
        # API indisponível SEM cache: indicador indisponível.
        assert resultado.origem == "indisponivel"
        assert resultado.indicador is None
        assert resultado.data_atualizacao is None


@pytest.mark.property
@given(
    indicator_id=st.sampled_from(_IDS),
    em_cache=_indicadores("SELIC"),
)
def test_prop09_indisponibilidade_sempre_usa_cache_quando_existe(
    indicator_id: str, em_cache: Indicador
) -> None:
    """Foco no invariante central: indisponível + cache ⟹ origem "cache".

    Garante que, sempre que a API estiver indisponível (``indicador_novo is
    None``) e existir um valor em cache para o ``indicator_id``, a origem é
    ``cache`` e a ``data_atualizacao`` é exatamente o timestamp do cache.
    """
    # Normaliza o indicador em cache para o ID consultado.
    em_cache_id = Indicador(
        indicator_id=indicator_id,
        date=em_cache.date,
        valor=em_cache.valor,
        valor_anterior=em_cache.valor_anterior,
        variacao_percentual=em_cache.variacao_percentual,
    )
    cache = {indicator_id: em_cache_id}

    resultado = resolver_com_fallback(indicator_id, None, cache)

    assert resultado.origem == "cache"
    assert resultado.indicador is em_cache_id
    assert resultado.data_atualizacao == em_cache_id.date
