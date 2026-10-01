# Infraestrutura (AWS CDK - Python)

Infraestrutura como código do backend, escrita em AWS CDK (Python). O deploy é
direcionado ao profile `contaA` na região `us-east-1`.

## Estrutura

```
infra/
├── app.py                 # Entrypoint do app CDK (instancia as stacks)
├── cdk.json               # Configuração do CDK (app, contexto, profile/região)
├── config.py              # Parâmetros de ambiente e nomes de recursos
├── requirements.txt       # Dependências do CDK (aws-cdk-lib v2, constructs)
└── stacks/                # Uma stack por camada da arquitetura
    ├── security_stack.py       # KMS, CloudFront/OAC, WAF, Cognito, CloudTrail
    ├── data_stack.py           # S3, DynamoDB, EventBridge, IAM
    ├── compute_stack.py        # As 6 funções Lambda
    ├── knowledge_base_stack.py # OpenSearch Serverless + Bedrock KB
    ├── agents_stack.py         # Amazon Bedrock AgentCore
    └── observability_stack.py  # CloudWatch (métricas, alarmes, dashboards)
```

As stacks são **esqueletos** nesta etapa (Tarefa 2.1). Os recursos de cada
camada são adicionados nas tarefas seguintes: segurança (2.2–2.6), dados
(2.3, 4.1–4.3), compute (5.x, 6.x), KB (8.x), agentes (9.x) e observabilidade
(11.x). O suporte de IaC do AgentCore é parcial; parte é provisionada
manualmente via AWS CLI/console (ver Tarefa 9.1).

## Ambiente e nomes de recursos

- A conta é resolvida pelo CDK a partir do profile `contaA`; a região padrão é
  `us-east-1` (`config.py` / `cdk.json`).
- O ambiente lógico é selecionado por contexto: `-c ambiente=dev` (padrão) ou
  `-c ambiente=prod`. Ambientes diferentes de `prod` recebem um prefixo nos
  nomes de recursos para evitar colisão de nomes globais (ex.: buckets S3).
- Os nomes de buckets, tabelas e Lambdas ficam centralizados em `config.py`
  (`NomesRecursos`), alinhados ao `design.md`.

## Uso

```bash
# 1. Criar/ativar um virtualenv e instalar as dependências do CDK
python3 -m pip install -r requirements.txt

# 2. Sintetizar o CloudFormation (não faz deploy)
cdk synth -c ambiente=dev --profile contaA

# 3. Deploy (requer confirmação explícita — ver diretrizes de segurança)
cdk deploy --all -c ambiente=dev --profile contaA
```

> O deploy é executado camada por camada nos checkpoints do plano (tarefas 3,
> 7, 10 e 13). Operações de deploy exigem confirmação explícita.
