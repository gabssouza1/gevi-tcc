"""Property test — Propriedade 10 (parte 1): separação de responsabilidades.

**Valida: Propriedade 10 do design / Requisito 14.2**

A Propriedade 10 exige que ``fn-consulta-APIs`` seja *apenas de escrita* em
relação à tabela ``EconomicIndicators`` (pipeline assíncrono). A leitura de
indicadores é responsabilidade exclusiva de ``fn-consulta-indicadores``.

Este módulo verifica, para entradas variadas (Hypothesis), que
``fn-consulta-APIs`` **nunca** executa operações de leitura
(``get_item``/``query``/``scan``/``batch_get_item``) sobre
``EconomicIndicators`` — somente ``put_item``. A verificação é feita
instrumentando o ``resource``/``Table`` do DynamoDB (moto) para registrar cada
operação chamada, sem alterar o comportamento real.

O handler é importado por caminho de arquivo (``importlib``) porque a pasta
``lambdas/fn-consulta-APIs`` contém hífen e não é um pacote Python importável.
"""

from __future__ import annotations

import importlib.util
import sys
from decimal import Decimal
from pathlib import Path

import boto3
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from moto import mock_aws

from shared import Indicador

# ---------------------------------------------------------------------------
# Importação do handler por caminho (a pasta tem hífen; não é pacote)
# ---------------------------------------------------------------------------
_RAIZ = Path(__file__).resolve().parents[1]  # .../sistema
_HANDLER_PATH = _RAIZ / "lambdas" / "fn-consulta-APIs" / "handler.py"

if str(_RAIZ) not in sys.path:  # garante que ``import shared`` funcione no handler
    sys.path.insert(0, str(_RAIZ))

_spec = importlib.util.spec_from_file_location("fn_consulta_apis_handler", _HANDLER_PATH)
assert _spec is not None and _spec.loader is not None
handler = importlib.util.module_from_spec(_spec)
# Registra o módulo em sys.modules antes de executá-lo: o processamento de
# ``@dataclass`` (Python 3.12+) resolve ``cls.__module__`` via ``sys.modules``.
sys.modules[_spec.name] = handler
_spec.loader.exec_module(handler)

# Operações de leitura do DynamoDB proibidas sobre EconomicIndicators (Prop. 10).
OPERACOES_LEITURA = {"get_item", "query", "scan", "batch_get_item", "get_item_iter"}
NOME_INDICADORES = "EconomicIndicators"
NOME_USERS = "Users"


# ---------------------------------------------------------------------------
# Instrumentação: espiões que registram operações do DynamoDB sem alterá-las
# ---------------------------------------------------------------------------
class TabelaEspia:
    """Encapsula uma ``Table`` real (moto) e registra as operações chamadas."""

    def __init__(self, tabela_real, registro: dict[str, list[str]], nome: str) -> None:
        self._real = tabela_real
        self._registro = registro
        self._nome = nome

    def __getattr__(self, atributo: str):
        alvo = getattr(self._real, atributo)
        if not callable(alvo):
            return alvo

        def _proxy(*args, **kwargs):
            self._registro.setdefault(self._nome, []).append(atributo)
            return alvo(*args, **kwargs)

        return _proxy


class ResourceEspiao:
    """Encapsula o ``service resource`` do DynamoDB e devolve ``TabelaEspia``."""

    def __init__(self, resource_real, registro: dict[str, list[str]]) -> None:
        self._real = resource_real
        self._registro = registro

    def Table(self, nome: str) -> TabelaEspia:  # noqa: N802 - assinatura do boto3
        return TabelaEspia(self._real.Table(nome), self._registro, nome)


def _criar_tabela_indicadores(dynamodb) -> None:
    dynamodb.create_table(
        TableName=NOME_INDICADORES,
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


def _criar_tabela_users(dynamodb) -> None:
    dynamodb.create_table(
        TableName=NOME_USERS,
        KeySchema=[{"AttributeName": "userId", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "userId", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )


def _assertar_somente_escrita(registro: dict[str, list[str]]) -> None:
    """Afirma que sobre EconomicIndicators só houve escrita (put_item)."""
    ops_indicadores = registro.get(NOME_INDICADORES, [])
    leituras = [op for op in ops_indicadores if op in OPERACOES_LEITURA]
    assert leituras == [], (
        f"fn-consulta-APIs executou leitura(s) proibida(s) em {NOME_INDICADORES}: "
        f"{leituras} (registro completo: {ops_indicadores})"
    )
    assert set(ops_indicadores) <= {"put_item"}, (
        f"Operações inesperadas em {NOME_INDICADORES}: {set(ops_indicadores)}"
    )


# ---------------------------------------------------------------------------
# Estratégias Hypothesis
# ---------------------------------------------------------------------------
# IDs de indicador válidos como partition key (não vazios, sem espaços).
_ids_indicador = st.text(
    alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd"), max_codepoint=122),
    min_size=1,
    max_size=12,
)

# Valores numéricos finitos e arredondados a 4 casas. O arredondamento evita
# magnitudes minúsculas (ex.: 1e-231) que o DynamoDB rejeita por underflow ao
# converter para Decimal — irrelevante para a propriedade de I/O sob teste.
_valores = st.floats(
    min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False
).map(lambda v: round(v, 4))

# moto + boto3 têm custo de inicialização acima do deadline padrão do Hypothesis;
# desligamos o deadline e a checagem de lentidão pois medem I/O simulado, não a lógica.
_config_moto = settings(deadline=None, suppress_health_check=[HealthCheck.too_slow])


def _construir_indicador(indicator_id: str, valor: float, timestamp: str) -> Indicador:
    """Monta um Indicador simples e válido para persistência (sem I/O)."""
    return Indicador(
        indicator_id=indicator_id,
        date=timestamp,
        valor=valor,
        valor_anterior=None,
        variacao_percentual=None,
    )


@pytest.mark.property
@pytest.mark.aws
@_config_moto
@given(indicator_id=_ids_indicador, valor=_valores)
def test_persistir_indicador_apenas_escreve(indicator_id: str, valor: float) -> None:
    """``persistir_indicador`` faz somente ``put_item`` em EconomicIndicators.

    Propriedade 10: a única operação de I/O de indicadores nesta função é
    escrita; nenhuma leitura (get/query/scan) é executada.
    """
    with mock_aws():
        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        _criar_tabela_indicadores(dynamodb)
        registro: dict[str, list[str]] = {}
        tabela = TabelaEspia(
            dynamodb.Table(NOME_INDICADORES), registro, NOME_INDICADORES
        )

        indicador = _construir_indicador(indicator_id, valor, "2024-01-01T00:00:00Z")
        handler.persistir_indicador(tabela, indicador)

        _assertar_somente_escrita(registro)
        assert registro[NOME_INDICADORES].count("put_item") == 1


@pytest.mark.property
@pytest.mark.aws
@_config_moto
@given(
    indicadores_brutos=st.dictionaries(
        keys=_ids_indicador, values=_valores, min_size=0, max_size=5
    )
)
def test_handler_nunca_le_economic_indicators(
    indicadores_brutos: dict[str, float]
) -> None:
    """O ``handler`` completo nunca lê EconomicIndicators, apenas escreve.

    Instrumenta o resource do DynamoDB e substitui as consultas de rede (BCB/B3)
    por dados gerados, isolando a propriedade de I/O de indicadores das chamadas
    HTTP externas. Sobre EconomicIndicators só pode haver ``put_item``.
    """
    timestamp = "2024-01-01T00:00:00Z"
    gerados = {
        iid: _construir_indicador(iid, valor, timestamp)
        for iid, valor in indicadores_brutos.items()
    }

    # Guarda e restaura o estado global do módulo entre exemplos do Hypothesis.
    bcb_original = handler.consultar_indicadores_bcb
    b3_original = handler.consultar_cotacoes_b3
    recurso_original = handler._recurso_dynamodb
    handler._CACHE_INDICADORES.clear()
    try:
        with mock_aws():
            dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
            _criar_tabela_indicadores(dynamodb)
            _criar_tabela_users(dynamodb)
            # Usuário com limiar para exercitar o caminho de notificação (lê Users).
            dynamodb.Table(NOME_USERS).put_item(
                Item={
                    "userId": "u-1",
                    "limiares_alerta": [
                        {"indicator_id": "SELIC", "limiar_percentual": Decimal("0.1")}
                    ],
                }
            )

            registro: dict[str, list[str]] = {}
            handler.consultar_indicadores_bcb = lambda ts, session=None: dict(gerados)
            handler.consultar_cotacoes_b3 = lambda ts, session=None: {}
            handler._recurso_dynamodb = lambda: ResourceEspiao(dynamodb, registro)

            handler.handler({}, None)

            _assertar_somente_escrita(registro)
    finally:
        handler.consultar_indicadores_bcb = bcb_original
        handler.consultar_cotacoes_b3 = b3_original
        handler._recurso_dynamodb = recurso_original
        handler._CACHE_INDICADORES.clear()


@pytest.mark.unit
def test_modulo_nao_expoe_leitura_de_indicadores() -> None:
    """Complemento estrutural: o módulo não possui função de leitura de indicadores.

    A leitura de EconomicIndicators pertence a ``fn-consulta-indicadores`` (Tarefa
    5.7). Nenhuma função com semântica de leitura de indicadores deve existir aqui.
    """
    nomes_proibidos = {
        "consultar_indicadores",
        "ler_indicadores",
        "obter_indicadores",
        "buscar_indicadores",
        "consultar_indicadores_persistidos",
    }
    presentes = nomes_proibidos & set(dir(handler))
    assert presentes == set(), (
        f"O módulo fn-consulta-APIs não deveria expor leitura de indicadores: {presentes}"
    )
    # A responsabilidade de escrita, por outro lado, deve estar presente.
    assert callable(handler.persistir_indicador)
