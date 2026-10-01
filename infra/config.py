"""Parâmetros e contexto de ambiente do app CDK.

Centraliza os nomes de recursos, a conta/região de deploy e demais parâmetros
usados pelas stacks. O deploy é direcionado ao profile ``contaA`` na região
``us-east-1`` (ver ``cdk.json`` e o README de ``infra/``).

Convenções de nome alinhadas ao ``design.md``:
- Buckets: ``s3-investimentos`` (frontend/datasets/kb-documentos) e ``audit-bucket``
- Tabelas DynamoDB: ``Users``, ``EconomicIndicators``, ``Portfolios``, ``Historico``
- Lambdas: ``fn-*`` (6 funções)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

# Região padrão de deploy (conforme design e tasks).
REGIAO_PADRAO = "us-east-1"

# Nome do profile AWS CLI usado no deploy. Não é consumido pelo código das
# stacks (é um conceito de CLI, informado via ``--profile contaA``), mas fica
# registrado aqui e no ``cdk.json`` para rastreabilidade.
PROFILE_PADRAO = "contaA"


@dataclass(frozen=True)
class NomesRecursos:
    """Nomes lógicos dos recursos AWS, reutilizados entre as stacks."""

    bucket_investimentos: str = "s3-investimentos"
    bucket_auditoria: str = "audit-bucket"

    tabela_users: str = "Users"
    tabela_indicadores: str = "EconomicIndicators"
    tabela_portfolios: str = "Portfolios"
    tabela_historico: str = "Historico"

    # Regra EventBridge do pipeline assíncrono de indicadores (a cada 5 min),
    # que aciona ``fn-consulta-APIs`` (ver design.md — Pipeline Assíncrono).
    regra_indicadores: str = "regra-indicadores"

    # Prefixos internos do bucket de investimentos (ver design.md).
    prefixos_investimentos: tuple[str, ...] = (
        "frontend/",
        "datasets/",
        "kb-documentos/",
    )

    # As 6 funções Lambda do backend.
    lambdas: tuple[str, ...] = (
        "fn-perfil-usuario",
        "fn-consulta-APIs",
        "fn-consulta-indicadores",
        "fn-calculo-simulacao",
        "fn-analise-portfolio",
        "fn-selecao-ativos",
    )


@dataclass(frozen=True)
class ConfigAmbiente:
    """Configuração de um ambiente de deploy (conta, região, prefixo de nomes)."""

    nome: str
    regiao: str = REGIAO_PADRAO
    conta: str | None = None
    prefixo: str = ""
    nomes: NomesRecursos = field(default_factory=NomesRecursos)

    def nome_recurso(self, base: str) -> str:
        """Aplica o prefixo do ambiente ao nome base de um recurso."""
        return f"{self.prefixo}{base}" if self.prefixo else base


def resolver_conta() -> str | None:
    """Resolve a conta AWS a partir do ambiente do CDK.

    Retorna ``CDK_DEFAULT_ACCOUNT`` (preenchido pelo CDK a partir do profile
    ``contaA``) ou ``None`` para uma stack agnóstica de ambiente.
    """
    return os.environ.get("CDK_DEFAULT_ACCOUNT")


def resolver_regiao() -> str:
    """Resolve a região de deploy, com ``us-east-1`` como padrão."""
    return os.environ.get("CDK_DEFAULT_REGION", REGIAO_PADRAO)


def carregar_config(nome_ambiente: str = "dev") -> ConfigAmbiente:
    """Constrói a configuração do ambiente informado.

    O nome do ambiente pode vir do contexto do CDK (``-c ambiente=...``); o
    padrão é ``dev``. Recursos ganham um prefixo por ambiente quando não for o
    ambiente ``prod``, evitando colisão de nomes globais (ex.: buckets S3).
    """
    prefixo = "" if nome_ambiente in ("prod", "") else f"{nome_ambiente}-"
    return ConfigAmbiente(
        nome=nome_ambiente,
        regiao=resolver_regiao(),
        conta=resolver_conta(),
        prefixo=prefixo,
    )
