"""Testes da camada de acesso ao Historico (Tarefas 9.9 e 9.10).

Valida ``shared.historico`` sobre a tabela ``Historico`` simulada (moto):

- ``gravar_recomendacao``: persiste a recomendação com ``tipo=RECOMENDACAO``.
- ``consultar_historico``: retorna itens do usuário ordenados por ``timestamp``
  (mais recentes primeiro) e filtra por ``tipo`` quando informado.
- resiliência: falha de escrita não propaga (retorna ``False``).
"""

from __future__ import annotations

import pytest

from shared import (
    TIPO_RECOMENDACAO,
    TIPO_SIMULACAO,
    consultar_historico,
    gravar_recomendacao,
    montar_item_recomendacao,
)


@pytest.fixture
def tabela_historico(dynamodb_simulado, nomes_tabelas):
    """Tabela ``Historico`` simulada (moto)."""
    return dynamodb_simulado.Table(nomes_tabelas["historico"])


@pytest.mark.aws
def test_gravar_e_consultar_recomendacao(tabela_historico):
    """Grava uma recomendação e a recupera pelo histórico do usuário."""
    item = montar_item_recomendacao(
        "u-1",
        "2026-01-01T10:00:00+00:00",
        ativos=[{"asset_id": "CDB-A", "nivel_risco": "BAIXO"}],
        tolerancia_risco="CONSERVADOR",
        indicadores={"CDI": 10.5},
    )
    assert gravar_recomendacao(tabela_historico, item) is True

    itens = consultar_historico(tabela_historico, "u-1")
    assert len(itens) == 1
    assert itens[0]["tipo"] == TIPO_RECOMENDACAO
    assert itens[0]["ativos"][0]["asset_id"] == "CDB-A"
    # Decimal do DynamoDB é convertido de volta para número.
    assert itens[0]["indicadores"]["CDI"] == 10.5


@pytest.mark.aws
def test_consulta_ordena_mais_recente_primeiro(tabela_historico):
    """A consulta retorna os itens do mais recente para o mais antigo."""
    for ts in ["2026-01-01T10:00:00+00:00", "2026-01-03T10:00:00+00:00",
               "2026-01-02T10:00:00+00:00"]:
        gravar_recomendacao(
            tabela_historico,
            montar_item_recomendacao("u-1", ts, ativos=[]),
        )
    itens = consultar_historico(tabela_historico, "u-1")
    timestamps = [i["timestamp"] for i in itens]
    assert timestamps == sorted(timestamps, reverse=True)


@pytest.mark.aws
def test_filtro_por_tipo(tabela_historico):
    """A consulta filtra por ``tipo`` (recomendações vs. simulações)."""
    gravar_recomendacao(
        tabela_historico,
        montar_item_recomendacao("u-1", "2026-01-01T10:00:00+00:00", ativos=[]),
    )
    # Simulação persistida diretamente (como faz fn-calculo-simulacao).
    tabela_historico.put_item(
        Item={
            "userId": "u-1",
            "timestamp": "2026-01-02T10:00:00+00:00",
            "tipo": TIPO_SIMULACAO,
        }
    )
    so_recomendacoes = consultar_historico(
        tabela_historico, "u-1", tipo=TIPO_RECOMENDACAO
    )
    assert len(so_recomendacoes) == 1
    assert so_recomendacoes[0]["tipo"] == TIPO_RECOMENDACAO

    todos = consultar_historico(tabela_historico, "u-1")
    assert len(todos) == 2


def test_gravar_recomendacao_resiliente_a_falha():
    """Falha ao persistir não propaga: retorna ``False`` (resiliência)."""

    class _TabelaQuebrada:
        def put_item(self, **_kwargs):
            raise RuntimeError("indisponível")

    ok = gravar_recomendacao(_TabelaQuebrada(), {"userId": "u", "timestamp": "t"})
    assert ok is False
