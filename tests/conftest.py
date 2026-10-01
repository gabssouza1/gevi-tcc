"""Fixtures base da suíte de testes (Tarefa 1.3).

Centraliza a configuração de infraestrutura AWS simulada com ``moto`` para que
testes unitários e property-based não acessem a nuvem. As fixtures criam as 4
tabelas DynamoDB e os 2 buckets S3 descritos no ``design.md``:

Tabelas DynamoDB
    - ``Users``              → PK ``userId``
    - ``EconomicIndicators`` → PK ``indicatorId`` + SK ``date``
    - ``Portfolios``         → PK ``userId`` + SK ``assetId``
    - ``Historico``          → PK ``userId`` + SK ``timestamp``

Buckets S3
    - ``s3-investimentos`` → frontend/, datasets/, kb-documentos/
    - ``audit-bucket``     → cloudtrail-logs/

Todas as fixtures usam ``@mock_aws`` (moto 5+) e credenciais falsas, garantindo
isolamento total da conta real. O escopo é ``function`` (padrão) para que cada
teste receba um ambiente limpo, evitando vazamento de estado entre casos.
"""

from __future__ import annotations

import os

import boto3
import pytest
from hypothesis import HealthCheck, settings
from moto import mock_aws

# ---------------------------------------------------------------------------
# Perfis do Hypothesis (property-based testing)
# ---------------------------------------------------------------------------
# - "dev"  → execução local rápida (menos exemplos, feedback ágil)
# - "ci"   → execução em CI mais exaustiva (mais exemplos, sem deadline)
# O perfil ativo é escolhido pela variável de ambiente HYPOTHESIS_PROFILE
# (padrão: "dev"). Ex.: `HYPOTHESIS_PROFILE=ci pytest`.
settings.register_profile("dev", max_examples=50)
settings.register_profile(
    "ci",
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile(os.getenv("HYPOTHESIS_PROFILE", "dev"))

# Região e credenciais falsas usadas apenas pelos mocks do moto. Definidas em
# nível de módulo para que sejam aplicadas antes de qualquer cliente boto3.
REGIAO_TESTE = "us-east-1"

# Nomes dos recursos, alinhados ao design.md.
TABELA_USERS = "Users"
TABELA_INDICADORES = "EconomicIndicators"
TABELA_PORTFOLIOS = "Portfolios"
TABELA_HISTORICO = "Historico"

BUCKET_INVESTIMENTOS = "s3-investimentos"
BUCKET_AUDITORIA = "audit-bucket"


@pytest.fixture(autouse=True)
def _credenciais_aws_falsas(monkeypatch: pytest.MonkeyPatch) -> None:
    """Injeta credenciais/região falsas para impedir acesso à conta real.

    Aplicada automaticamente (``autouse``) a todos os testes: mesmo que um teste
    esqueça de usar os mocks, o boto3 nunca encontrará credenciais reais.
    """
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGIAO_TESTE)


@pytest.fixture
def aws_simulada():
    """Ativa o mock global da AWS (moto 5+) durante o teste.

    Uso: peça esta fixture quando precisar de qualquer serviço AWS simulado sem
    tabelas/buckets pré-criados. As fixtures ``dynamodb_simulado`` e
    ``s3_simulado`` já a incluem.
    """
    with mock_aws():
        yield


@pytest.fixture
def dynamodb_simulado(aws_simulada):
    """Cria as 4 tabelas DynamoDB do design e devolve o ``resource`` boto3.

    Retorna o ``service resource`` do DynamoDB já com as tabelas ``Users``,
    ``EconomicIndicators``, ``Portfolios`` e ``Historico`` criadas em modo
    on-demand (``PAY_PER_REQUEST``), como no design.
    """
    dynamodb = boto3.resource("dynamodb", region_name=REGIAO_TESTE)

    dynamodb.create_table(
        TableName=TABELA_USERS,
        KeySchema=[{"AttributeName": "userId", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "userId", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )

    dynamodb.create_table(
        TableName=TABELA_INDICADORES,
        KeySchema=[
            {"AttributeName": "indicatorId", "KeyType": "HASH"},
            {"AttributeName": "date", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "indicatorId", "AttributeType": "S"},
            {"AttributeName": "date", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )

    dynamodb.create_table(
        TableName=TABELA_PORTFOLIOS,
        KeySchema=[
            {"AttributeName": "userId", "KeyType": "HASH"},
            {"AttributeName": "assetId", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "userId", "AttributeType": "S"},
            {"AttributeName": "assetId", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )

    dynamodb.create_table(
        TableName=TABELA_HISTORICO,
        KeySchema=[
            {"AttributeName": "userId", "KeyType": "HASH"},
            {"AttributeName": "timestamp", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "userId", "AttributeType": "S"},
            {"AttributeName": "timestamp", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )

    return dynamodb


@pytest.fixture
def s3_simulado(aws_simulada):
    """Cria os 2 buckets S3 do design e devolve o ``client`` boto3.

    Retorna o ``client`` do S3 já com ``s3-investimentos`` e ``audit-bucket``
    criados. Os buckets ficam vazios; cada teste insere apenas os objetos de que
    precisa.
    """
    s3 = boto3.client("s3", region_name=REGIAO_TESTE)
    # us-east-1 não aceita LocationConstraint; criação simples é suficiente.
    s3.create_bucket(Bucket=BUCKET_INVESTIMENTOS)
    s3.create_bucket(Bucket=BUCKET_AUDITORIA)
    return s3


@pytest.fixture
def nomes_tabelas() -> dict[str, str]:
    """Mapa amigável nome-lógico → nome-da-tabela, reutilizável nos testes."""
    return {
        "users": TABELA_USERS,
        "indicadores": TABELA_INDICADORES,
        "portfolios": TABELA_PORTFOLIOS,
        "historico": TABELA_HISTORICO,
    }


@pytest.fixture
def nomes_buckets() -> dict[str, str]:
    """Mapa amigável nome-lógico → nome-do-bucket, reutilizável nos testes."""
    return {
        "investimentos": BUCKET_INVESTIMENTOS,
        "auditoria": BUCKET_AUDITORIA,
    }
