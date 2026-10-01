"""Propriedade 5 — Disparo de Alertas por Limiar (Tarefa 5.5).

**Valida: Requisitos 8.2, 8.4**

Propriedade de Corretude: um alerta in-app é disparado se, e somente se, o
módulo da variação percentual do indicador excede (estritamente, ``>``) o
limiar configurado pelo usuário. Em particular, a borda ``|variação| == limiar``
NÃO dispara alerta.

O teste exercita as duas funções puras de ``fn-consulta-APIs``:

- ``variacao_excede_limiar`` — o predicado que decide o disparo;
- ``avaliar_alertas`` — a avaliação de alto nível que percorre limiares e
  indicadores e monta as notificações in-app.

A lógica é pura (sem rede nem AWS), então o teste importa o ``handler.py``
diretamente via ``importlib`` (o diretório da Lambda contém hífen e não é um
pacote importável pelo caminho normal).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from shared import Indicador, LimiarAlerta

# ---------------------------------------------------------------------------
# Carregamento do handler da Lambda (diretório com hífen → importlib direto)
# ---------------------------------------------------------------------------
_RAIZ = Path(__file__).resolve().parents[1]
if str(_RAIZ) not in sys.path:  # garante que ``shared`` seja importável
    sys.path.insert(0, str(_RAIZ))

_HANDLER_PATH = _RAIZ / "lambdas" / "fn-consulta-APIs" / "handler.py"
_spec = importlib.util.spec_from_file_location("fn_consulta_apis_handler", _HANDLER_PATH)
assert _spec is not None and _spec.loader is not None
handler = importlib.util.module_from_spec(_spec)
# Registra o módulo antes de executá-lo: no Python 3.14 o ``dataclass`` resolve
# anotações via ``sys.modules[cls.__module__]``, que precisa já existir.
sys.modules[_spec.name] = handler
_spec.loader.exec_module(handler)

variacao_excede_limiar = handler.variacao_excede_limiar
avaliar_alertas = handler.avaliar_alertas

# ---------------------------------------------------------------------------
# Estratégias de geração
# ---------------------------------------------------------------------------
# Variações percentuais: qualquer float finito (positivo, negativo ou zero).
variacoes = st.floats(min_value=-1000.0, max_value=1000.0, allow_nan=False, allow_infinity=False)
# Limiares configurados pelo usuário: percentuais não negativos.
limiares = st.floats(min_value=0.0, max_value=1000.0, allow_nan=False, allow_infinity=False)

TIMESTAMP = "2024-01-01T00:00:00+00:00"
INDICATOR_ID = "SELIC"


# ---------------------------------------------------------------------------
# Propriedade sobre o predicado ``variacao_excede_limiar``
# ---------------------------------------------------------------------------
@pytest.mark.property
@given(variacao=variacoes, limiar=limiares)
def test_predicado_dispara_sse_modulo_excede_limiar(variacao: float, limiar: float) -> None:
    """O predicado retorna ``True`` se, e somente se, ``|variação| > limiar``."""
    assert variacao_excede_limiar(variacao, limiar) == (abs(variacao) > limiar)


@pytest.mark.property
@given(limiar=limiares)
def test_predicado_na_borda_nao_dispara(limiar: float) -> None:
    """Na borda (``|variação| == limiar``) o alerta NÃO dispara (``>`` estrito)."""
    assert variacao_excede_limiar(limiar, limiar) is False
    assert variacao_excede_limiar(-limiar, limiar) is False


# ---------------------------------------------------------------------------
# Propriedade sobre a avaliação de alto nível ``avaliar_alertas``
# ---------------------------------------------------------------------------
def _construir(variacao: float | None, limiar: float) -> tuple[list[LimiarAlerta], dict[str, Indicador]]:
    """Monta um par (limiares, indicadores) para um único indicador."""
    limiar_alerta = LimiarAlerta(indicator_id=INDICATOR_ID, limiar_percentual=limiar)
    indicador = Indicador(
        indicator_id=INDICATOR_ID,
        date=TIMESTAMP,
        valor=100.0,
        valor_anterior=100.0,
        variacao_percentual=variacao,
    )
    return [limiar_alerta], {INDICATOR_ID: indicador}


@pytest.mark.property
@given(variacao=variacoes, limiar=limiares)
def test_avaliar_alertas_notifica_sse_excede_limiar(variacao: float, limiar: float) -> None:
    """``avaliar_alertas`` gera notificação exatamente quando ``|variação| > limiar``."""
    lim, ind = _construir(variacao, limiar)
    notificacoes = avaliar_alertas(lim, ind, TIMESTAMP)

    deve_disparar = abs(variacao) > limiar
    assert (len(notificacoes) == 1) is deve_disparar
    if deve_disparar:
        notificacao = notificacoes[0]
        assert notificacao["tipo"] == "ALERTA_MERCADO"
        assert notificacao["indicatorId"] == INDICATOR_ID
        assert notificacao["limiar_percentual"] == limiar
        assert notificacao["variacao_percentual"] == variacao


@pytest.mark.property
@given(limiar=limiares)
def test_avaliar_alertas_na_borda_nao_notifica(limiar: float) -> None:
    """Na borda (``|variação| == limiar``) nenhuma notificação é gerada."""
    for variacao in (limiar, -limiar):
        lim, ind = _construir(variacao, limiar)
        assert avaliar_alertas(lim, ind, TIMESTAMP) == []


@pytest.mark.property
@given(limiar=limiares)
def test_avaliar_alertas_ignora_variacao_desconhecida(limiar: float) -> None:
    """Indicador sem variação (``None``) nunca dispara alerta."""
    lim, ind = _construir(None, limiar)
    assert avaliar_alertas(lim, ind, TIMESTAMP) == []
