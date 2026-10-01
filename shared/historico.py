"""Camada de acesso ao ``Historico`` (recomendações e simulações).

Contrato reutilizável de escrita e consulta da tabela DynamoDB ``Historico``
(PK ``userId`` + SK ``timestamp``), onde ficam as recomendações (Tarefa 9.9) e
as simulações (Tarefa 6.8) geradas para o usuário, de forma consultável e
rastreável (design.md — Tabelas DynamoDB; Requisitos 2.5, 2.6, 2.7).

As funções recebem um ``tabela`` (recurso boto3 ``Table``) por parâmetro — não
importam boto3 — para manter o pacote ``shared`` leve e testável com moto.

Marcador ``tipo``: cada item carrega ``tipo`` (``RECOMENDACAO`` ou ``SIMULACAO``)
para distinguir os dois na mesma tabela ao consultar (Tarefa 9.10).
"""

from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import Any

from boto3.dynamodb.conditions import Key

logger = logging.getLogger(__name__)

# Marcadores de tipo de registro no Historico.
TIPO_RECOMENDACAO = "RECOMENDACAO"
TIPO_SIMULACAO = "SIMULACAO"

# Limite padrão de itens retornados na consulta ao histórico.
LIMITE_CONSULTA_PADRAO = 20


def _para_dynamo(item: dict[str, Any]) -> dict[str, Any]:
    """Serializa o item para tipos aceitos pelo DynamoDB (float → Decimal)."""
    return json.loads(json.dumps(item), parse_float=Decimal)


def _de_dynamo(valor: Any) -> Any:
    """Converte recursivamente ``Decimal`` (DynamoDB) de volta para int/float."""
    if isinstance(valor, list):
        return [_de_dynamo(v) for v in valor]
    if isinstance(valor, dict):
        return {k: _de_dynamo(v) for k, v in valor.items()}
    if isinstance(valor, Decimal):
        inteiro = int(valor)
        return inteiro if valor == inteiro else float(valor)
    return valor


def montar_item_recomendacao(
    user_id: str,
    timestamp: str,
    *,
    ativos: list[dict[str, Any]],
    tolerancia_risco: str | None = None,
    indicadores: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Monta o item consultável de uma recomendação para o ``Historico``.

    Usa a partition key ``userId`` e a sort key ``timestamp``, permitindo
    consulta por usuário ordenada no tempo. O ``tipo`` é ``RECOMENDACAO``.
    """
    return {
        "userId": user_id,
        "timestamp": timestamp,
        "tipo": TIPO_RECOMENDACAO,
        "ativos": ativos,
        "tolerancia_risco": tolerancia_risco,
        "indicadores": indicadores or {},
    }


def gravar_recomendacao(tabela: Any, item: dict[str, Any]) -> bool:
    """Grava a recomendação no ``Historico`` de forma resiliente (Tarefa 9.9).

    Converte os campos para tipos do DynamoDB e persiste. Uma falha ao persistir
    é registrada em log e NÃO propaga (a recomendação ainda é devolvida ao
    usuário); retorna ``True`` em sucesso e ``False`` em falha.
    """
    try:
        tabela.put_item(Item=_para_dynamo(item))
        return True
    except Exception as exc:  # noqa: BLE001 - persistência não deve derrubar a resposta
        logger.warning("Falha ao persistir recomendação no Historico: %s", exc)
        return False


def consultar_historico(
    tabela: Any,
    user_id: str,
    *,
    tipo: str | None = None,
    limite: int = LIMITE_CONSULTA_PADRAO,
) -> list[dict[str, Any]]:
    """Consulta o histórico de um usuário ordenado por ``timestamp`` (Tarefa 9.10).

    Retorna as recomendações e simulações persistidas para ``user_id``, das mais
    recentes para as mais antigas (``ScanIndexForward=False``). Quando ``tipo`` é
    informado (``RECOMENDACAO`` ou ``SIMULACAO``), filtra por esse tipo. Atende à
    apresentação do histórico previamente persistido (Requisito 2.7).
    """
    argumentos: dict[str, Any] = {
        "KeyConditionExpression": Key("userId").eq(user_id),
        "ScanIndexForward": False,
        "Limit": limite,
    }
    if tipo:
        from boto3.dynamodb.conditions import Attr

        argumentos["FilterExpression"] = Attr("tipo").eq(tipo)

    resposta = tabela.query(**argumentos)
    return [_de_dynamo(item) for item in resposta.get("Items", [])]
