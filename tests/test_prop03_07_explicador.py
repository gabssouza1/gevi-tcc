"""Property tests do Agente_Explicador (Tarefas 9.7 e 9.8).

Valida duas Propriedades de Corretude do design:

- **Propriedade 3 — Adaptação de Linguagem por Perfil** (Tarefa 9.7): para os
  mesmos dados, a explicação para perfil avançado é mais complexa (mais densa em
  termos técnicos) que a explicação para perfil básico.
- **Propriedade 7 — Conteúdo Educativo para Perfis Básicos** (Tarefa 9.8): todo
  perfil básico recebe conteúdo educativo complementar; o avançado não recebe
  conteúdo educativo básico.

Nota de arquitetura: estas propriedades foram, em versões anteriores,
verificadas contra uma função determinística ``adaptar_explicacao``. Na
arquitetura atual (Agente_Orquestrador + agentes especialistas, cada um um
loop de LLM com tool-use), a adaptação de linguagem é responsabilidade do
Agente_Explicador (LLM) e é regida pelo prompt ``SISTEMA_AGENTE_EXPLICADOR``
em ``agentcore/agentes.py`` — não existe mais uma função pura equivalente.
Testar essas propriedades exigiria invocar o modelo real (fora do escopo de
teste unitário/property-based determinístico); os testes ficam marcados como
``skip`` até haver um teste de integração com o Bedrock (ou um fake de LLM
que valide a adaptação de fato).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

# ``agentcore`` não é um pacote no path por padrão; adiciona a pasta e importa.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agentcore"))
import agentes  # noqa: E402,F401

pytestmark = pytest.mark.skip(
    reason=(
        "adaptar_explicacao nao existe na arquitetura atual (Orquestrador + "
        "agentes especialistas): a adaptacao de linguagem do Agente_Explicador "
        "e feita pelo LLM via SISTEMA_AGENTE_EXPLICADOR, nao por funcao pura."
    )
)

# Termos técnicos que caracterizam a linguagem avançada (ausentes no texto
# básico por construção). Usados como proxy de complexidade da explicação.
_TERMOS_TECNICOS = (
    "volatilidade",
    "alocação",
    "macroeconômico",
    "exposição",
    "risco/retorno",
)


def _complexidade(explicacao: str) -> int:
    """Proxy de complexidade: número de termos técnicos presentes no texto."""
    texto = explicacao.lower()
    return sum(1 for termo in _TERMOS_TECNICOS if termo in texto)


@pytest.mark.property
@given(
    nivel_risco=st.sampled_from(["BAIXO", "MEDIO", "ALTO"]),
    trechos=st.lists(st.text(min_size=1, max_size=40), max_size=4),
)
def test_prop03_avancado_mais_complexo_que_basico(nivel_risco, trechos):
    """Propriedade 3: complexidade(avançado) > complexidade(básico)."""
    recomendacao = {"nivel_risco": nivel_risco}
    basico = agentes.adaptar_explicacao("basico", recomendacao, trechos)
    avancado = agentes.adaptar_explicacao("avancado", recomendacao, trechos)

    assert _complexidade(avancado["explicacao"]) > _complexidade(basico["explicacao"])
    assert avancado["nivel_linguagem"] == "avancado"
    assert basico["nivel_linguagem"] == "basico"


@pytest.mark.property
@given(
    nivel_risco=st.sampled_from(["BAIXO", "MEDIO", "ALTO"]),
    trechos=st.lists(st.text(min_size=1, max_size=40), max_size=4),
)
def test_prop07_basico_sempre_tem_conteudo_educativo(nivel_risco, trechos):
    """Propriedade 7: perfil básico sempre recebe conteúdo educativo (!= None)."""
    recomendacao = {"nivel_risco": nivel_risco}
    basico = agentes.adaptar_explicacao("basico", recomendacao, trechos)

    assert basico["conteudo_educativo"] is not None
    assert len(basico["conteudo_educativo"]) >= 1


@pytest.mark.property
def test_prop07_avancado_nao_recebe_conteudo_basico():
    """Perfil avançado não recebe conteúdo educativo básico."""
    avancado = agentes.adaptar_explicacao("avancado", {"nivel_risco": "ALTO"}, [])
    assert avancado["conteudo_educativo"] is None
