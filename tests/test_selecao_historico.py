"""Testes de histórico na fn-selecao-ativos (Tarefas 9.9 e 9.10).

- 9.9: ao selecionar ativos com ``user_id``, a recomendação é persistida no
  ``Historico``.
- 9.10: com ``operacao='consultar_historico'``, a Lambda retorna o histórico do
  usuário.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
from boto3.dynamodb.conditions import Key

from shared import TIPO_RECOMENDACAO

_CAMINHO_HANDLER = (
    Path(__file__).resolve().parents[1] / "lambdas" / "fn-selecao-ativos" / "handler.py"
)


def _carregar_handler() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "fn_selecao_ativos_handler", _CAMINHO_HANDLER
    )
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


handler_mod = _carregar_handler()


@pytest.fixture
def tabelas(dynamodb_simulado, nomes_tabelas):
    """Tabelas Users/EconomicIndicators/Historico simuladas (moto)."""
    return {
        "users": dynamodb_simulado.Table(nomes_tabelas["users"]),
        "indicadores": dynamodb_simulado.Table(nomes_tabelas["indicadores"]),
        "historico": dynamodb_simulado.Table(nomes_tabelas["historico"]),
    }


@pytest.mark.aws
def test_selecao_persiste_recomendacao_no_historico(tabelas):
    """9.9: a seleção com tolerância informada persiste a recomendação."""
    resposta = handler_mod.processar(
        {"tolerancia_risco": "MODERADO", "user_id": "u-1"},
        tabelas["users"],
        tabelas["indicadores"],
        tabelas["historico"],
    )
    assert resposta["sucesso"] is True

    itens = tabelas["historico"].query(
        KeyConditionExpression=Key("userId").eq("u-1")
    )["Items"]
    assert len(itens) == 1
    assert itens[0]["tipo"] == TIPO_RECOMENDACAO


@pytest.mark.aws
def test_consultar_historico_operacao(tabelas):
    """9.10: operacao='consultar_historico' retorna o histórico do usuário."""
    # Primeiro gera e persiste uma recomendação.
    handler_mod.processar(
        {"tolerancia_risco": "ARROJADO", "user_id": "u-2"},
        tabelas["users"],
        tabelas["indicadores"],
        tabelas["historico"],
    )
    # Depois consulta o histórico via operação dedicada.
    resposta = handler_mod.processar(
        {"operacao": "consultar_historico", "user_id": "u-2"},
        tabelas["users"],
        tabelas["indicadores"],
        tabelas["historico"],
    )
    assert resposta["sucesso"] is True
    assert len(resposta["historico"]) == 1
    assert resposta["historico"][0]["tipo"] == TIPO_RECOMENDACAO


@pytest.mark.aws
def test_consultar_historico_sem_user_id(tabelas):
    """Consulta sem user_id retorna erro de dados inválidos."""
    resposta = handler_mod.processar(
        {"operacao": "consultar_historico"},
        tabelas["users"],
        tabelas["indicadores"],
        tabelas["historico"],
    )
    assert resposta["sucesso"] is False
