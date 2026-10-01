"""Property test — Propriedade 10 (lado da leitura) — Tarefa 5.8.

**Propriedade 10: Separação de Responsabilidades das Lambdas de Indicadores**

    ``fn-consulta-APIs`` apenas escreve; ``fn-consulta-indicadores`` apenas lê.

    Verificação (design):
        ``fn-consulta-indicadores.operacoes ⊆ {read}``

Este arquivo cobre o lado da **leitura**: a função ``fn-consulta-indicadores``
nunca deve escrever na tabela ``EconomicIndicators`` (apenas ``query``/``get``).
Para provar isso, envolvemos a tabela DynamoDB simulada (moto) em uma *espiã*
que registra todos os métodos invocados e afirmamos a ausência de qualquer
operação de escrita (``put_item``/``update_item``/``delete_item``/etc.).

Complementa com uma propriedade da função pura ``calcular_variacao_percentual``
(coerência de sinal e retorno ``None`` quando o valor anterior é ``0`` ou
``None``).

**Validates: Requisito 7.4 / Requisito 10 (design)**
"""

from __future__ import annotations

import importlib.util
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from moto import mock_aws

import boto3

# ---------------------------------------------------------------------------
# Importação do handler de ``lambdas/fn-consulta-indicadores/handler.py``.
# A pasta usa hífens (não é um pacote Python importável por nome), então
# carregamos o módulo diretamente pelo caminho de arquivo via importlib.
# O ``from shared import ...`` interno funciona porque ``shared`` está instalado
# no ambiente de testes.
# ---------------------------------------------------------------------------
_RAIZ = Path(__file__).resolve().parents[1]
_HANDLER_PATH = _RAIZ / "lambdas" / "fn-consulta-indicadores" / "handler.py"

_spec = importlib.util.spec_from_file_location(
    "fn_consulta_indicadores_handler", _HANDLER_PATH
)
assert _spec is not None and _spec.loader is not None
handler_mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = handler_mod
_spec.loader.exec_module(handler_mod)


REGIAO_TESTE = "us-east-1"
TABELA = "EconomicIndicators"
INDICADORES = ("SELIC", "IPCA", "DOLAR", "CDI")


# ---------------------------------------------------------------------------
# Tabela espiã: registra todos os métodos invocados para provar somente-leitura.
# ---------------------------------------------------------------------------
class TabelaEspia:
    """Envolve uma tabela DynamoDB registrando os métodos invocados.

    Delega todas as chamadas à tabela real (moto) e guarda o nome de cada
    método chamado, permitindo afirmar que nenhuma operação de escrita ocorreu.
    """

    OPERACOES_ESCRITA = frozenset(
        {
            "put_item",
            "update_item",
            "delete_item",
            "batch_writer",
            "batch_write_item",
            "transact_write_items",
        }
    )

    def __init__(self, tabela_real: Any) -> None:
        self._tabela = tabela_real
        self.chamadas: list[str] = []

    def __getattr__(self, nome: str) -> Any:
        atributo = getattr(self._tabela, nome)
        if callable(atributo):

            def _proxy(*args: Any, **kwargs: Any) -> Any:
                self.chamadas.append(nome)
                return atributo(*args, **kwargs)

            return _proxy
        # Acesso a atributo simples (ex.: .name) também é registrado.
        self.chamadas.append(nome)
        return atributo

    @property
    def operacoes_de_escrita(self) -> list[str]:
        return [c for c in self.chamadas if c in self.OPERACOES_ESCRITA]


def _criar_tabela() -> Any:
    """Cria a tabela ``EconomicIndicators`` na AWS simulada (moto ativo)."""
    dynamodb = boto3.resource("dynamodb", region_name=REGIAO_TESTE)
    dynamodb.create_table(
        TableName=TABELA,
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
    return dynamodb.Table(TABELA)


# Estratégias -----------------------------------------------------------------
def _entrada() -> st.SearchStrategy[tuple[str, float]]:
    """Gera uma entrada (data ISO-8601, valor) para um indicador."""
    datas = st.dates().map(lambda d: d.isoformat())
    valores = st.floats(
        min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False
    )
    return st.tuples(datas, valores)


def _semear(tabela: Any, dados: dict[str, list[tuple[str, float]]]) -> None:
    """Popula a tabela real com os itens gerados (escrita só no setup)."""
    for indicator_id, entradas in dados.items():
        for data, valor in entradas:
            tabela.put_item(
                Item={
                    "indicatorId": indicator_id,
                    "date": data,
                    # Arredonda para 2 casas: evita expoentes extremos que
                    # estourariam o contexto Decimal do DynamoDB (limite do
                    # gerador, não do código sob teste).
                    "valor": Decimal(str(round(valor, 2))),
                }
            )


# ---------------------------------------------------------------------------
# Propriedade 10 (leitura): consultar_indicadores nunca escreve.
# ---------------------------------------------------------------------------
@pytest.mark.property
@given(
    dados=st.dictionaries(
        keys=st.sampled_from(INDICADORES),
        values=st.lists(_entrada(), min_size=0, max_size=4),
        max_size=len(INDICADORES),
    ),
    max_idade=st.one_of(
        st.none(),
        st.floats(min_value=0, max_value=1e6, allow_nan=False, allow_infinity=False),
    ),
)
@settings(deadline=None)  # setup do moto varia; a corretude não depende de tempo
def test_consultar_indicadores_nunca_escreve(
    dados: dict[str, list[tuple[str, float]]], max_idade: float | None
) -> None:
    """``consultar_indicadores`` só usa operações de leitura (Propriedade 10).

    **Validates: Requisito 7.4 / Requisito 10 (design)**
    """
    with mock_aws():
        tabela_real = _criar_tabela()
        _semear(tabela_real, dados)  # escrita apenas no setup, fora da espiã

        espia = TabelaEspia(tabela_real)
        resultado = handler_mod.consultar_indicadores(
            espia, list(INDICADORES), max_idade_horas=max_idade
        )

        # Nenhuma operação de escrita pode ter sido invocada pela função.
        assert espia.operacoes_de_escrita == []
        # E ao menos uma leitura (query) deve ter ocorrido por indicador.
        assert espia.chamadas.count("query") == len(INDICADORES)
        assert isinstance(resultado, list)
        assert len(resultado) == len(INDICADORES)


@pytest.mark.property
@given(
    indicadores=st.lists(
        st.sampled_from(INDICADORES), unique=True, min_size=1, max_size=len(INDICADORES)
    ),
)
@settings(deadline=None)  # setup do moto varia; a corretude não depende de tempo
def test_handler_nunca_escreve(indicadores: list[str]) -> None:
    """O ``handler`` completo também nunca escreve em ``EconomicIndicators``.

    **Validates: Requisito 7.4 / Requisito 10 (design)**
    """
    with mock_aws():
        tabela_real = _criar_tabela()
        _semear(
            tabela_real,
            {ind: [("2024-01-10", 10.0), ("2024-01-05", 9.0)] for ind in indicadores},
        )

        espia = TabelaEspia(tabela_real)
        original = handler_mod._obter_tabela
        handler_mod._obter_tabela = lambda: espia  # type: ignore[assignment]
        try:
            resposta = handler_mod.handler({"indicadores": indicadores}, None)
        finally:
            handler_mod._obter_tabela = original  # type: ignore[assignment]

        assert espia.operacoes_de_escrita == []
        assert "indicadores" in resposta
        assert len(resposta["indicadores"]) == len(indicadores)


# ---------------------------------------------------------------------------
# Propriedade da função pura calcular_variacao_percentual.
# ---------------------------------------------------------------------------
@pytest.mark.property
@given(
    atual=st.floats(
        min_value=-1e9, max_value=1e9, allow_nan=False, allow_infinity=False
    ),
    anterior=st.one_of(
        st.none(),
        st.floats(min_value=-1e9, max_value=1e9, allow_nan=False, allow_infinity=False),
    ),
)
def test_variacao_percentual_sinal_coerente(
    atual: float, anterior: float | None
) -> None:
    """Sinal coerente e ``None`` quando o valor anterior é ``0``/``None``.

    **Validates: Requisito 7.3 / Requisito 10 (design)**
    """
    resultado = handler_mod.calcular_variacao_percentual(atual, anterior)

    if anterior is None or anterior == 0:
        # Sem período anterior (ou divisor zero) → indefinido.
        assert resultado is None
        return

    assert resultado is not None
    diferenca = atual - anterior
    if diferenca == 0:
        # Sem variação → 0%.
        assert resultado == 0
    else:
        # A variação divide por |anterior| (>0), logo o sinal segue a diferença.
        assert (resultado > 0) == (diferenca > 0)
        assert (resultado < 0) == (diferenca < 0)
