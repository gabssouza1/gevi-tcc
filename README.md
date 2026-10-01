# GEVI — Assistente Multiagente de Investimentos

Plataforma de apoio à decisão para investidores pessoa física, construída como
TCC. O GEVI ajuda o usuário a entender o cenário econômico, organizar a própria
carteira e receber recomendações e explicações personalizadas ao seu perfil.

> O GEVI é uma **plataforma de apoio**: não executa compra, venda ou qualquer
> transação de ativos. As projeções são baseadas em dados históricos e não
> garantem resultados futuros.

## Visão geral

- **Frontend**: aplicação Next.js exportada como site estático, servida por
  CloudFront + S3, com autenticação no Amazon Cognito.
- **Backend**: um HTTP API (API Gateway) autorizado pelo Cognito encaminha as
  requisições para uma Lambda proxy (`fn-api-proxy`), que fala com o Amazon
  Bedrock AgentCore (Runtime do agente + tools do Gateway) e com o DynamoDB.
- **Agente**: um orquestrador LLM (Claude Opus 4.5) decide, a cada turno, quais
  ferramentas acionar (padrão *tool use*), reunindo cinco especialidades:
  Perfil, Macroeconômico, Risco, Seleção de Ativos e Explicador (educação).
- **Dados reais**: indicadores macroeconômicos (Selic, IPCA, Dólar, CDI) vêm do
  Banco Central e são normalizados para base anual (% a.a.).

## Arquitetura (fluxo de uma pergunta no chat)

1. O navegador autentica no Cognito e chama o HTTP API com o token do usuário.
2. `fn-api-proxy` valida o token, cria um job de chat e dispara um worker
   assíncrono (contorna o limite de 30s do API Gateway).
3. O worker invoca o Runtime do AgentCore, repassando o JWT do usuário.
4. O orquestrador (Opus) lê o perfil, consulta indicadores, analisa a carteira
   e seleciona ativos acionando as tools do Gateway (Lambdas de dados).
5. A resposta é gravada no job; o frontend faz *polling* até concluir.

## Componentes

- **Frontend (`front-app/`)**: Next.js estático. Telas: onboarding
  (questionário de perfil), início/dashboard, carteira (investimentos), chat,
  minha conta e configurações.
- **Proxy (`lambdas/fn-api-proxy/`)**: ponte entre o frontend e o AgentCore.
  Trata chat assíncrono (job + polling), histórico de conversas, cadastro da
  carteira e o repasse das tools do Gateway. Ver `lambdas/README.md`.
- **AgentCore**:
  - *Gateway* — valida o JWT do Cognito e roteia as tools (Lambdas de dados).
  - *Runtime* — contêiner (`agentcore/`) que hospeda o orquestrador.
  - *Memory* — memória do agente: curto prazo (turnos da conversa) e longo
    prazo (preferências e fatos do investidor entre conversas).
- **Lambdas de dados (`lambdas/`)**: perfil, indicadores, simulação de risco,
  análise de portfólio e seleção de ativos. Acionadas como tools pelo Gateway.
- **Dados**: DynamoDB (`Users`, `EconomicIndicators`, `Portfolios`,
  `Historico`, `ChatJobs`, `ChatConversas`), Knowledge Base (RAG de educação
  financeira) e S3 (assets do frontend e datasets).

## Agente e modelo

- **Modelo**: `us.anthropic.claude-opus-4-5-20251101-v1:0` (invocado via
  inference profile na Bedrock Converse API).
- **Orquestração**: em vez de um pipeline sequencial fixo, o agente decide
  quais tools chamar por *tool use*, até produzir a resposta final adaptada ao
  nível de conhecimento do usuário (`básico` ou `avançado`).
- **Agente_Perfil**: a classificação de suitability é **determinística** por
  pontuação do questionário (6–9 Conservador, 10–13 Moderado, 14–18 Arrojado),
  com regra de consistência (baixa tolerância a perdas rebaixa Arrojado para
  Moderado). O LLM apenas redige a justificativa.

## Funcionalidades

- **Onboarding com questionário**: no primeiro login sem perfil, o usuário
  responde ao questionário e a IA identifica o perfil de investidor.
- **Chat assíncrono**: recomendação completa via job + polling (sem estourar o
  teto de 30s do API Gateway).
- **Histórico de conversas**: cada conversa é salva (`ChatConversas`) e pode
  ser reaberta pela barra lateral do chat.
- **Memória do agente (AgentCore Memory)**: a IA lembra do contexto da conversa
  e das preferências/fatos do investidor entre conversas. O DynamoDB serve a
  lista da UI; a Memory dá o contexto ao agente (abordagem híbrida).
- **Carteira manual**: o usuário cadastra seus ativos (nome, tipo e valor) na
  página Carteira. Os dados vão para `Portfolios`, a mesma tabela que a tool
  `analisar_portfolio` lê — então a análise da IA usa a carteira real.
- **Dashboard com dados reais**: indicadores do BCB, alocação e total da
  carteira, e o perfil de risco do usuário.
- **Indicadores normalizados**: taxas convertidas para base anual (% a.a.).

## Divergências em relação ao design original

A implementação evoluiu além do desenho inicial. As principais diferenças:

- **Orquestrador LLM** (tool use) no lugar do pipeline sequencial fixo dos 5
  agentes.
- **Modelo Claude Opus 4.5** no lugar de `openai.gpt-oss-120b` citado no design.
- **Chat assíncrono** (job + polling) no proxy, em vez de resposta síncrona.
- **Memória do agente ativa**: o container passou a usar o AgentCore Memory
  (curto e longo prazo), que antes existia só como recurso provisionado.
- **Carteira por cadastro manual** (sem integração com corretora e sem
  transações), coerente com o posicionamento de plataforma de apoio. A
  **escrita** da carteira é feita pelo proxy direto no DynamoDB (ações
  `listar_portfolio`/`salvar_portfolio`), tratando o cadastro como um CRUD da
  aplicação; a **leitura da IA** continua pela tool `analisar_portfolio`
  (Gateway → `fn-analise-portfolio`). Decisão pragmática: evita criar uma tool
  de escrita no Gateway (e a mudança de CDK correspondente), reaproveitando o
  padrão já usado por `ChatJobs`/`ChatConversas`.

### Recursos criados/ajustados fora do CDK (via CLI)

Para reprodutibilidade, estes itens foram aplicados por CLI e não estão no CDK:

- Tabelas DynamoDB `ChatJobs` (jobs de chat, com TTL) e `ChatConversas`
  (histórico de conversas).
- Policy IAM inline `InvocarClaudeOpus45` na role do Runtime.
- Policy IAM inline `Portfolios` na role do proxy (acesso à tabela
  `Portfolios`).
- Variáveis de ambiente e timeout do proxy (`JOBS_TABLE`, `RUNTIME_ARN`,
  `GATEWAY_URL`, etc.).
- Imagem do container do agente publicada no ECR, com o Runtime apontado para
  a tag atual via `cdk deploy MAIA-dev-Agents`.

## Build e deploy

Pré-requisitos: AWS CLI configurada, Node.js, Python 3.12+, podman e o
`aws-cdk` v2. Sempre exportar a região ao usar o CDK
(`AWS_REGION=us-east-1`), senão ele tenta `sa-east-1`.

Antes de rodar o frontend, configure `.env.local` em `front-app/`:

```
NEXT_PUBLIC_API_URL=https://<SEU_API_GATEWAY_ID>.execute-api.us-east-1.amazonaws.com
NEXT_PUBLIC_COGNITO_USER_POOL_ID=us-east-1_<SEU_POOL_ID>
NEXT_PUBLIC_COGNITO_CLIENT_ID=<SEU_CLIENT_ID>
```

**Frontend** (em `front-app/`):

```bash
npm run build
aws s3 sync out/ s3://<SEU_BUCKET_S3>/frontend/ --delete --profile <SEU_PERFIL>
aws cloudfront create-invalidation --distribution-id <SEU_DIST_ID> --paths "/*" --profile <SEU_PERFIL>
```

**Proxy** (em `lambdas/fn-api-proxy/`):

```bash
zip -j /tmp/fn-api-proxy.zip handler.py
aws lambda update-function-code --function-name <SEU_PREFIXO>-fn-api-proxy \
  --zip-file fileb:///tmp/fn-api-proxy.zip --profile <SEU_PERFIL> --region us-east-1
```

**Container do agente** (em `agentcore/`, build ARM64 + push ECR + repontar):

```bash
URI=<ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com/<SEU_REPO_ECR>:<tag>
podman build --platform linux/arm64 -t "$URI" .
aws ecr get-login-password --profile <SEU_PERFIL> --region us-east-1 | \
  podman login --username AWS --password-stdin <ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com
podman push "$URI"
# em infra/: aponta o Runtime para a nova imagem (preserva env e headers)
AWS_PROFILE=<SEU_PERFIL> AWS_REGION=us-east-1 npx --yes aws-cdk@2 \
  deploy MAIA-dev-Agents -c ambiente=dev -c imagem_agentes=$URI --require-approval never
```

## Estrutura do repositório

- `front-app/` — frontend Next.js.
- `agentcore/` — imagem do Runtime (orquestrador + servidor HTTP + memória).
- `lambdas/` — proxy e Lambdas de dados (tools).
- `infra/` — CDK (stacks) e notas de IaC do AgentCore.
- `shared/` — modelos e utilitários comuns às Lambdas.
- `kb-documentos/` — conteúdo da Knowledge Base (RAG).
- `tests/` — testes unitários e de propriedade.
