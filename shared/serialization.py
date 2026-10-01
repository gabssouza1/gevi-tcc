"""Funções genéricas de (de)serialização dos modelos compartilhados.

Fornecem conversão entre as dataclasses de ``shared.models`` e dicionários
Python simples (``dict``/``list``/tipos primitivos), reutilizadas pelas Lambdas
para persistir e ler itens do DynamoDB de forma consistente.

- ``to_dict``   → converte uma dataclass (ou lista/dict aninhado) em ``dict``,
  serializando enumerações para o seu ``value`` textual.
- ``from_dict`` → reconstrói uma dataclass a partir de um ``dict``, respeitando
  os tipos anotados (enums, dataclasses aninhadas, ``list[...]`` e ``X | None``).

O objetivo é evitar que cada Lambda reimplemente a serialização, mantendo baixo
acoplamento entre o código comum e as funções (Requisito 13.2).
"""

from __future__ import annotations

import types
import typing
from dataclasses import fields, is_dataclass
from enum import Enum
from typing import Any, TypeVar

T = TypeVar("T")


def to_dict(obj: Any) -> Any:
    """Serializa recursivamente uma dataclass/enum/coleção em tipos simples.

    Enumerações são convertidas para o seu ``value`` textual; dataclasses viram
    ``dict``; listas e dicionários são percorridos recursivamente. Valores
    primitivos (str, int, float, bool, None) são retornados sem alteração.
    """
    if is_dataclass(obj) and not isinstance(obj, type):
        return {campo.name: to_dict(getattr(obj, campo.name)) for campo in fields(obj)}
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, (list, tuple)):
        return [to_dict(item) for item in obj]
    if isinstance(obj, dict):
        return {chave: to_dict(valor) for chave, valor in obj.items()}
    return obj


def from_dict(cls: type[T], data: dict[str, Any]) -> T:
    """Reconstrói uma dataclass ``cls`` a partir de um ``dict``.

    Campos ausentes no ``dict`` recorrem ao valor padrão definido na dataclass.
    Os tipos são resolvidos a partir das anotações da classe, cobrindo enums,
    dataclasses aninhadas, ``list[...]`` e uniões opcionais (``X | None``).
    """
    if not is_dataclass(cls):
        raise TypeError(f"{cls!r} não é uma dataclass")
    if not isinstance(data, dict):
        raise TypeError(f"esperado dict para {cls.__name__}, recebido {type(data).__name__}")

    anotacoes = typing.get_type_hints(cls)
    kwargs: dict[str, Any] = {}
    for campo in fields(cls):
        if campo.name not in data:
            # Deixa a dataclass aplicar o valor padrão (ou falhar se obrigatório).
            continue
        kwargs[campo.name] = _converter(anotacoes[campo.name], data[campo.name])
    return cls(**kwargs)  # type: ignore[return-value]


def _converter(tipo: Any, valor: Any) -> Any:
    """Converte ``valor`` para o ``tipo`` anotado, de forma recursiva."""
    if valor is None:
        return None

    origem = typing.get_origin(tipo)

    # União / Optional (ex.: ``float | None``): usa o primeiro tipo não-None.
    if origem is typing.Union or origem is types.UnionType:
        tipos_validos = [arg for arg in typing.get_args(tipo) if arg is not type(None)]
        if not tipos_validos:
            return valor
        return _converter(tipos_validos[0], valor)

    # Listas (ex.: ``list[AtivoPortfolio]``): converte cada item.
    if origem in (list, tuple):
        (tipo_item,) = typing.get_args(tipo) or (Any,)
        return [_converter(tipo_item, item) for item in valor]

    # Enumerações de domínio: reconstrói a partir do ``value``.
    if isinstance(tipo, type) and issubclass(tipo, Enum):
        return tipo(valor)

    # Dataclasses aninhadas: recursão.
    if is_dataclass(tipo):
        return from_dict(tipo, valor)

    return valor
