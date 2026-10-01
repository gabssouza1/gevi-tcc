#!/usr/bin/env python3
"""Entrypoint do app CDK do backend (Sistema Multiagente de Investimentos).

Instancia as stacks da arquitetura serverless descrita no ``design.md``. O
deploy é direcionado ao profile ``contaA`` na região ``us-east-1`` (informe o
profile via ``cdk deploy --profile contaA``; a conta é resolvida pelo CDK).

Ambiente selecionável por contexto: ``cdk synth -c ambiente=dev`` (padrão
``dev``). As stacks recebem a configuração de ``config.py`` e, nesta etapa, são
esqueletos preenchidos nas tarefas 2.2–2.6, 4.x, 5.x, 6.x, 8.x, 9.x e 11.x.
"""

from __future__ import annotations

import aws_cdk as cdk

from config import carregar_config
from stacks import (
    AgentsStack,
    ApiStack,
    ComputeStack,
    DataStack,
    FrontendStack,
    KnowledgeBaseStack,
    ObservabilityStack,
    SecurityStack,
)

app = cdk.App()

# Ambiente lógico (dev/prod/...) via contexto do CDK; padrão "dev".
nome_ambiente = app.node.try_get_context("ambiente") or "dev"
config = carregar_config(nome_ambiente)

# Ambiente de deploy do CloudFormation (conta + região). A conta vem do CDK
# (resolvida a partir do profile contaA); a região padrão é us-east-1.
env = cdk.Environment(account=config.conta, region=config.regiao)

# Prefixo dos nomes das stacks por ambiente (evita colisão entre ambientes).
prefixo_stack = f"MAIA-{config.nome}"

# Tags aplicadas a todos os recursos do app para rastreabilidade.
tags = {
    "Projeto": "multiagent-investment-advisor",
    "Ambiente": config.nome,
    "IaC": "CDK",
}

comuns = {"config": config, "env": env, "tags": tags}

# Camada de segurança/rede: KMS, CloudFront/OAC, WAF, Cognito.
seguranca = SecurityStack(app, f"{prefixo_stack}-Security", **comuns)

# Camada de dados: S3, CloudTrail, DynamoDB, EventBridge, roles IAM. Recebe a
# chave KMS da SecurityStack (tarefa 2.2) para criptografia em repouso dos
# buckets e das tabelas; a referência entre stacks cria a dependência de deploy
# adequada.
dados = DataStack(
    app,
    f"{prefixo_stack}-Data",
    chave_kms=seguranca.chave_kms,
    **comuns,
)

# Camada de borda (tarefa 2.4): CloudFront + OAC + WAF servindo o frontend a
# partir de s3-investimentos/frontend/. Fica em uma stack dedicada que recebe o
# bucket da DataStack por parâmetro, mantendo a cadeia de dependências linear
# (Security -> Data -> Frontend) e evitando o ciclo que surgiria ao colocar o
# CloudFront na SecurityStack referenciando o bucket da DataStack.
frontend = FrontendStack(
    app,
    f"{prefixo_stack}-Frontend",
    nome_bucket_investimentos=dados.bucket_investimentos.bucket_name,
    **comuns,
)
# O bucket é importado por nome na FrontendStack (sem token), então não há
# dependência de recurso implícita. A ordem de deploy (bucket antes da
# distribuição) é garantida por uma dependência explícita de stack.
frontend.add_dependency(dados)

# Camada de compute: as 6 funções Lambda. Recebe as tabelas, o bucket e as
# roles de menor privilégio da DataStack (tarefas 4.1/4.3) para ligar cada
# handler à sua role e às variáveis de ambiente (nomes físicos das tabelas/bucket).
# A dependência resultante é linear: Security -> Data -> Compute.
compute = ComputeStack(
    app,
    f"{prefixo_stack}-Compute",
    tabela_users=dados.tabela_users,
    tabela_indicadores=dados.tabela_indicadores,
    tabela_portfolios=dados.tabela_portfolios,
    tabela_historico=dados.tabela_historico,
    bucket_investimentos=dados.bucket_investimentos,
    roles_lambdas=dados.roles_lambdas,
    **comuns,
)

# Knowledge Base (RAG): OpenSearch Serverless + Bedrock KB. Recebe o bucket
# s3-investimentos da DataStack para indexar o prefixo kb-documentos/. A
# referência cria a dependência KnowledgeBase -> Data (direção correta); a
# DataStack não referencia a KB, então não há ciclo entre as stacks.
kb = KnowledgeBaseStack(
    app,
    f"{prefixo_stack}-KnowledgeBase",
    bucket_investimentos=dados.bucket_investimentos,
    **comuns,
)

# Agentes: Amazon Bedrock AgentCore. Recebe o Cognito (SecurityStack) para o
# authorizer JWT do Gateway, as 6 funções (ComputeStack) para os GatewayTargets
# das tools, a chave KMS (SecurityStack) e a Knowledge Base (KnowledgeBaseStack)
# para o RAG do Agente_Explicador. Dependências one-directional:
# Agents -> {Security, Compute, KnowledgeBase}.
agentes = AgentsStack(
    app,
    f"{prefixo_stack}-Agents",
    user_pool=seguranca.user_pool,
    user_pool_client=seguranca.user_pool_client,
    funcoes=compute.funcoes,
    knowledge_base_id=kb.knowledge_base_id,
    knowledge_base_arn=kb.knowledge_base.attr_knowledge_base_arn,
    **comuns,
)

# Observabilidade: CloudWatch (métricas, alarmes, dashboards). Recebe as 6
# Lambdas (Compute), as 4 tabelas (Data) e o Gateway/Runtime (Agents) para
# alarmes e o dashboard de saúde. Dependências one-directional (leaf consumer).
observabilidade = ObservabilityStack(
    app,
    f"{prefixo_stack}-Observability",
    funcoes=compute.funcoes,
    tabelas={
        "users": dados.tabela_users,
        "indicadores": dados.tabela_indicadores,
        "portfolios": dados.tabela_portfolios,
        "historico": dados.tabela_historico,
    },
    gateway=agentes.gateway,
    runtime=agentes.runtime,
    **comuns,
)

# Camada de API (opção "b" de integração do frontend): HTTP API + Lambda proxy
# autorizado pelo Cognito, que repassa o JWT do usuário ao AgentCore — /chat
# (Runtime) e /gateway/{tool} (tools do Gateway, dados reais). Depende do Cognito
# (SecurityStack) e do Gateway/Runtime (AgentsStack). A origem de CORS é o domínio
# do CloudFront (FrontendStack). O Runtime pode não existir ainda (sem imagem);
# nesse caso o proxy expõe só as tools do Gateway e /chat responde 503.
api = ApiStack(
    app,
    f"{prefixo_stack}-Api",
    user_pool=seguranca.user_pool,
    user_pool_client=seguranca.user_pool_client,
    gateway_url=agentes.gateway.gateway_url,
    allowed_origin=f"https://{frontend.distribuicao.distribution_domain_name}",
    runtime_arn=(
        agentes.runtime.agent_runtime_arn if agentes.runtime is not None else None
    ),
    runtime_qualifier=(
        agentes.runtime_endpoint.endpoint_name
        if agentes.runtime_endpoint is not None
        else None
    ),
    **comuns,
)
api.add_dependency(agentes)
api.add_dependency(frontend)

# -----------------------------------------------------------------------------
# Tarefa 12.1 — Contratos expostos ao frontend (fora de escopo deste plano).
# CfnOutputs documentam, de forma legível por máquina, os identificadores que a
# UI (React/Next.js/Amplify) consumirá: Cognito (autenticação), URL do Gateway
# do AgentCore (chamadas ao backend), domínio do CloudFront e bucket dos assets.
# Cada output é criado no escopo da stack dona do recurso.
# -----------------------------------------------------------------------------
cdk.CfnOutput(
    seguranca,
    "SaidaUserPoolId",
    value=seguranca.user_pool.user_pool_id,
    description="Cognito User Pool ID (autenticação do frontend).",
)
cdk.CfnOutput(
    seguranca,
    "SaidaUserPoolClientId",
    value=seguranca.user_pool_client.user_pool_client_id,
    description="Cognito App Client ID (integração do frontend).",
)
cdk.CfnOutput(
    dados,
    "SaidaBucketInvestimentos",
    value=dados.bucket_investimentos.bucket_name,
    description="Bucket s3-investimentos (frontend/, datasets/, kb-documentos/).",
)
cdk.CfnOutput(
    frontend,
    "SaidaCloudFrontDominio",
    value=frontend.distribuicao.distribution_domain_name,
    description="Domínio do CloudFront que serve o frontend.",
)
cdk.CfnOutput(
    agentes,
    "SaidaGatewayUrl",
    value=agentes.gateway.gateway_url,
    description="URL do AgentCore Gateway (entrada das chamadas do frontend).",
)
if agentes.runtime_endpoint is not None:
    cdk.CfnOutput(
        agentes,
        "SaidaRuntimeEndpointArn",
        value=agentes.runtime_endpoint.agent_runtime_endpoint_arn,
        description="ARN do endpoint do Runtime dos agentes.",
    )

app.synth()
