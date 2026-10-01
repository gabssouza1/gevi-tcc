#!/usr/bin/env python3
"""Configuração CLI do Amazon Bedrock AgentCore (tarefa 9.6).

Cobre o que NÃO é declarável por IaC e depende de artefatos de build (ver a
decisão em ``infra/AGENTCORE_IAC.md``): a plataforma (Gateway, Identity, Memory,
Runtime) fica na ``AgentsStack`` (CDK); aqui construímos e publicamos a imagem
de contêiner dos 5 agentes e verificamos o acesso ao modelo.

Passos (idempotentes):

1. Resolver a conta AWS (``sts get-caller-identity``) e montar a URI do ECR.
2. Verificar (best-effort) o acesso ao modelo ``openai.gpt-oss-120b-1:0``.
3. Build da imagem (``agentcore/``) e push para o repositório ECR
   ``<ambiente>-maia-agentes`` criado pela ``AgentsStack``. Docker build/push são
   idempotentes por digest — reexecutar só publica o que mudou.
4. Imprimir a URI da imagem e o comando de deploy do Runtime:
   ``cdk deploy MAIA-<ambiente>-Agents -c imagem_agentes=<uri>``.

O Runtime do AgentCore só é criado (na ``AgentsStack``) quando a URI da imagem é
passada no contexto ``imagem_agentes`` — por isso este script imprime o comando
pronto. Antes da imagem existir, o deploy provisiona apenas a plataforma.

Segurança: o script NÃO cria nem apaga recursos AWS (o repositório ECR é criado
pela stack); apenas autentica no ECR, faz build/push da imagem e lê a identidade
da conta. Requer Docker e credenciais válidas (profile ``contaA``).
"""

from __future__ import annotations

import argparse
import base64
import subprocess
import sys
from pathlib import Path

MODELO_LLM = "openai.gpt-oss-120b-1:0"

# Contexto de build da imagem (pasta com Dockerfile + agentes.py + servidor.py).
_RAIZ = Path(__file__).resolve().parent.parent
_CONTEXTO_BUILD = _RAIZ / "agentcore"


def _nome_repo(ambiente: str) -> str:
    """Repositório ECR criado pela AgentsStack (prefixo por ambiente)."""
    return "maia-agentes" if ambiente == "prod" else f"{ambiente}-maia-agentes"


def _sessao(region: str, profile: str | None):
    """Cria uma sessão boto3 (opcionalmente com profile)."""
    import boto3

    return boto3.Session(profile_name=profile, region_name=region)


def _resolver_conta(sessao) -> str:
    """Resolve o ID da conta via STS."""
    return sessao.client("sts").get_caller_identity()["Account"]


def _verificar_acesso_modelo(sessao) -> bool:
    """Verifica (best-effort) se o modelo do LLM está disponível na conta.

    Usa ``bedrock list-foundation-models``. A entitlement (acesso concedido) é
    gerenciada no console; aqui apenas sinalizamos se o modelo aparece na região.
    Falhas não interrompem o fluxo (retorna ``False`` com aviso).
    """
    try:
        bedrock = sessao.client("bedrock")
        modelos = bedrock.list_foundation_models().get("modelSummaries", [])
        return any(m.get("modelId") == MODELO_LLM for m in modelos)
    except Exception as exc:  # noqa: BLE001 - verificação best-effort
        print(f"  aviso: não foi possível verificar o modelo ({exc})")
        return False


def _login_ecr(sessao, conta: str, region: str) -> str:
    """Autentica o Docker no ECR e devolve a URI base do registry."""
    ecr = sessao.client("ecr")
    token = ecr.get_authorization_token()["authorizationData"][0]
    usuario, senha = (
        base64.b64decode(token["authorizationToken"]).decode().split(":", 1)
    )
    registry = f"{conta}.dkr.ecr.{region}.amazonaws.com"
    subprocess.run(
        ["docker", "login", "--username", usuario, "--password-stdin", registry],
        input=senha.encode(),
        check=True,
    )
    return registry


def _build_push(registry: str, repo: str, tag: str) -> str:
    """Faz build (linux/amd64) e push da imagem para o ECR. Retorna a URI.

    Usa ``docker buildx build --platform linux/arm64 --push`` para: (a) produzir
    a arquitetura que o AgentCore Runtime EXIGE (``arm64`` — o serviço rejeita
    amd64); e (b) construir e publicar em um único passo, compatível com o driver
    ``docker-container`` do buildx (em que ``docker build`` só popula o cache, sem
    carregar no daemon local). Em Apple Silicon o build arm64 é nativo.
    """
    uri = f"{registry}/{repo}:{tag}"
    subprocess.run(
        [
            "docker",
            "buildx",
            "build",
            "--platform",
            "linux/arm64",
            "--push",
            "-t",
            uri,
            str(_CONTEXTO_BUILD),
        ],
        check=True,
    )
    return uri


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build/push da imagem dos agentes e verificação do modelo."
    )
    parser.add_argument("--region", default="us-east-1", help="Região AWS.")
    parser.add_argument("--profile", default=None, help="Profile AWS (ex.: contaA).")
    parser.add_argument("--ambiente", default="dev", help="Ambiente (dev/prod/...).")
    parser.add_argument("--tag", default="latest", help="Tag da imagem (padrão latest).")
    parser.add_argument(
        "--skip-model-check",
        action="store_true",
        help="Pula a verificação de acesso ao modelo.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Só imprime o que seria feito, sem build/push.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Constrói e publica a imagem dos agentes; imprime o comando de deploy."""
    args = _parse_args(argv)
    repo = _nome_repo(args.ambiente)
    print(f"AgentCore — configuração (ambiente={args.ambiente}, região={args.region})")

    if args.dry_run:
        print("  [dry-run] nenhuma ação executada. Passos que seriam feitos:")
        print(f"   1. sts get-caller-identity (resolver conta)")
        print(f"   2. verificar modelo {MODELO_LLM}")
        print(f"   3. docker build {_CONTEXTO_BUILD} + push para {repo}:{args.tag}")
        print(f"   4. imprimir URI e comando 'cdk deploy ... -c imagem_agentes=<uri>'")
        return 0

    sessao = _sessao(args.region, args.profile)
    conta = _resolver_conta(sessao)
    print(f"  conta={conta}")

    if not args.skip_model_check:
        ok = _verificar_acesso_modelo(sessao)
        estado = "disponível" if ok else "NÃO confirmado (habilite no console)"
        print(f"  modelo {MODELO_LLM}: {estado}")

    try:
        registry = _login_ecr(sessao, conta, args.region)
        uri = _build_push(registry, repo, args.tag)
    except FileNotFoundError:
        print("  erro: Docker não encontrado no PATH.", file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as exc:
        print(f"  erro no build/push: {exc}", file=sys.stderr)
        return exc.returncode or 1

    print(f"\nImagem publicada: {uri}")
    print("Deploy do Runtime (cria/atualiza o Runtime na AgentsStack):")
    print(
        f"  cdk deploy MAIA-{args.ambiente}-Agents "
        f'-c ambiente={args.ambiente} -c imagem_agentes={uri} --profile {args.profile or "contaA"}'
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
