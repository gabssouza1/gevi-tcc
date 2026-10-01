"""Testes de fumaça da suíte (Tarefa 1.3).

Provam que o framework de testes está corretamente configurado:

- o pacote ``shared`` é importável e o round-trip de (de)serialização funciona;
- as fixtures de DynamoDB e S3 simulados (moto) criam as 4 tabelas e os 2
  buckets do design;
- o Hypothesis roda com o perfil ativo.

Não cobrem as Propriedades de Corretude do design (isso é feito nas tarefas de
teste específicas); servem apenas para validar que a suíte executa.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from boto3.dynamodb.conditions import Key
from hypothesis import given
from hypothesis import strategies as st

from shared import (
    Nivel_Conhecimento,
    Perfil_Investidor,
    Perfil_Risco,
    from_dict,
    to_dict,
    validar_perfil_investidor,
)

# ---------------------------------------------------------------------------
# Carrega o handler de ``lambdas/fn-calculo-simulacao/handler.py`` (pasta com
# hífens não é importável por nome) para o smoke test de persistência (6.8).
# ---------------------------------------------------------------------------
_RAIZ = Path(__file__).resolve().parents[1]
_HANDLER_SIMULACAO = _RAIZ / "lambdas" / "fn-calculo-simulacao" / "handler.py"
_spec = importlib.util.spec_from_file_location(
    "fn_calculo_simulacao_handler_smoke", _HANDLER_SIMULACAO
)
assert _spec is not None and _spec.loader is not None
simulacao_mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = simulacao_mod
_spec.loader.exec_module(simulacao_mod)


@pytest.mark.smoke
def test_import_shared_expoe_api_publica() -> None:
    """O pacote ``shared`` expõe enums, modelos e helpers esperados."""
    assert Perfil_Risco.MODERADO.value == "MODERADO"
    assert Nivel_Conhecimento.BASICO.value == "basico"


@pytest.mark.unit
def test_round_trip_serializacao_perfil() -> None:
    """``to_dict``/``from_dict`` preservam um Perfil_Investidor (round-trip)."""
    perfil = Perfil_Investidor(
        user_id="u-1",
        email="investidor@exemplo.com",
        nome="Investidor Exemplo",
        nivel_conhecimento=Nivel_Conhecimento.BASICO,
        tolerancia_risco=Perfil_Risco.CONSERVADOR,
        objetivos=["aposentadoria"],
    )

    bruto = to_dict(perfil)
    assert bruto["tolerancia_risco"] == "CONSERVADOR"

    reconstruido = from_dict(Perfil_Investidor, bruto)
    assert reconstruido == perfil
    assert validar_perfil_investidor(reconstruido) is reconstruido


@pytest.mark.property
@given(
    nome=st.text(min_size=1, max_size=40).filter(lambda s: s.strip()),
    perfil=st.sampled_from(list(Perfil_Risco)),
    conhecimento=st.sampled_from(list(Nivel_Conhecimento)),
)
def test_round_trip_property(nome: str, perfil: Perfil_Risco, conhecimento: Nivel_Conhecimento) -> None:
    """Para qualquer perfil válido, o round-trip de serialização é idempotente."""
    original = Perfil_Investidor(
        user_id="u-prop",
        email="a@b.co",
        nome=nome,
        nivel_conhecimento=conhecimento,
        tolerancia_risco=perfil,
    )
    assert from_dict(Perfil_Investidor, to_dict(original)) == original


@pytest.mark.aws
def test_fixture_dynamodb_cria_quatro_tabelas(dynamodb_simulado, nomes_tabelas) -> None:
    """A fixture de DynamoDB cria exatamente as 4 tabelas do design."""
    tabelas_existentes = {t.name for t in dynamodb_simulado.tables.all()}
    assert tabelas_existentes == set(nomes_tabelas.values())


@pytest.mark.aws
def test_fixture_dynamodb_round_trip_item(dynamodb_simulado, nomes_tabelas) -> None:
    """É possível gravar e ler um item nas tabelas simuladas."""
    tabela = dynamodb_simulado.Table(nomes_tabelas["users"])
    tabela.put_item(Item={"userId": "u-1", "nome": "Teste"})
    resposta = tabela.get_item(Key={"userId": "u-1"})
    assert resposta["Item"]["nome"] == "Teste"


@pytest.mark.aws
def test_fixture_s3_cria_dois_buckets(s3_simulado, nomes_buckets) -> None:
    """A fixture de S3 cria os 2 buckets do design."""
    buckets = {b["Name"] for b in s3_simulado.list_buckets()["Buckets"]}
    assert set(nomes_buckets.values()).issubset(buckets)


@pytest.mark.aws
def test_fixture_s3_round_trip_objeto(s3_simulado, nomes_buckets) -> None:
    """É possível gravar e ler um objeto no bucket simulado."""
    s3_simulado.put_object(
        Bucket=nomes_buckets["investimentos"],
        Key="datasets/exemplo.txt",
        Body=b"conteudo",
    )
    obj = s3_simulado.get_object(
        Bucket=nomes_buckets["investimentos"], Key="datasets/exemplo.txt"
    )
    assert obj["Body"].read() == b"conteudo"


@pytest.mark.aws
def test_simulacao_persistida_no_historico(
    monkeypatch, dynamodb_simulado, nomes_tabelas
) -> None:
    """Simulação de usuário real é gravada no ``Historico`` e é consultável (Req 2.6)."""
    monkeypatch.setenv("TABELA_HISTORICO", nomes_tabelas["historico"])

    resposta = simulacao_mod.handler(
        {
            "user_id": "u-42",
            "valor_inicial": 1000.0,
            "prazo_meses": 12,
            "tipo_ativo": "RENDA_FIXA",
            "indicadores": {"CDI": 10.0, "IPCA": 4.0},
        },
        None,
    )

    assert resposta["disponivel"] is True
    assert resposta["persistido"] is True

    # O item deve ser consultável por userId (PK) ordenado por timestamp (SK).
    tabela = dynamodb_simulado.Table(nomes_tabelas["historico"])
    itens = tabela.query(KeyConditionExpression=Key("userId").eq("u-42"))["Items"]
    assert len(itens) == 1
    item = itens[0]
    assert item["timestamp"] == resposta["gerado_em"]
    assert item["tipo_registro"] == simulacao_mod.TIPO_REGISTRO_SIMULACAO
    assert item["simulacao"]["tipo_ativo"] == "RENDA_FIXA"
    assert "analise_risco" in item


@pytest.mark.aws
def test_simulacao_anonima_nao_persistida(
    monkeypatch, dynamodb_simulado, nomes_tabelas
) -> None:
    """Simulação anônima (sem ``user_id``) não é gravada no ``Historico`` (Req 2.6)."""
    monkeypatch.setenv("TABELA_HISTORICO", nomes_tabelas["historico"])

    resposta = simulacao_mod.handler(
        {
            "valor_inicial": 1000.0,
            "prazo_meses": 12,
            "tipo_ativo": "RENDA_FIXA",
            "indicadores": {"CDI": 10.0, "IPCA": 4.0},
        },
        None,
    )

    assert resposta["disponivel"] is True
    assert resposta["persistido"] is False

    tabela = dynamodb_simulado.Table(nomes_tabelas["historico"])
    itens = tabela.query(KeyConditionExpression=Key("userId").eq("desconhecido"))["Items"]
    assert itens == []
