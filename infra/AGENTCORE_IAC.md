# Suporte de IaC do Amazon Bedrock AgentCore (Tarefa 9.1)

Registro da verificação do estado atual do suporte a Infraestrutura como Código
(CloudFormation/CDK) do Amazon Bedrock AgentCore, cobrindo Gateway, Runtime,
Identity e Memory. Documenta o que pode ser provisionado por IaC, o que exige
AWS CLI/console e o plano recomendado para as tarefas 9.2, 9.3, 9.5 e 9.6.

_Requisito: 13.1 (escalabilidade/manutenibilidade — o Runtime deve suportar
adição de agentes sem alterar os existentes)._

## Decisão (resumo)

A maior parte da infraestrutura do AgentCore **já é provisionável via IaC**. A
família de recursos `AWS::BedrockAgentCore::*` existe no CloudFormation e está
exposta no `aws-cdk-lib` (módulo `aws_cdk.aws_bedrockagentcore`), com recursos
L1 (`Cfn*`) e um conjunto crescente de constructs L2. Isso **atualiza** a
premissa anterior do `design.md`/`tasks.md`, escrita quando o AgentCore era
recém-lançado, de que "o suporte de IaC seria parcial ou inexistente".

O que **não** é declarável como recurso de IaC é a *lógica dos agentes*: os 5
agentes não são recursos AWS individuais. Eles são código de aplicação
(orquestração + prompts) empacotado em uma imagem de contêiner e implantado em
um `AWS::BedrockAgentCore::Runtime`. Portanto:

- A **plataforma** (Gateway, Runtime, Memory, identidades/credenciais, targets
  de tools) fica na `AgentsStack` via CDK.
- O **empacotamento e publicação da imagem do agente** (build + push para ECR)
  e a **habilitação de acesso ao modelo** no Bedrock são feitos por scripts
  CLI idempotentes em `scripts/` e/ou console, pois dependem de artefatos de
  build, não de definição declarativa.

## Estado atual da implementação

Atualização em relação ao que este documento previa:

- **Memory em uso pelo agente.** O container (`agentcore/servidor.py`) passou a
  usar a AgentCore Memory de fato: carrega o histórico de curto prazo da
  conversa (`list_events`), recupera preferências/fatos de longo prazo
  (`retrieve_memory_records`) e grava cada turno (`create_event`). O `MEMORY_ID`
  é injetado como variável de ambiente do Runtime; a role já tem as permissões
  do data plane. O DynamoDB (`ChatConversas`) serve a lista da UI; a Memory dá
  o contexto ao agente (abordagem híbrida).
- **Modelo.** O agente usa `us.anthropic.claude-opus-4-5-20251101-v1:0`, e não
  `openai.gpt-oss-120b-1:0`. A permissão de invocação foi concedida por uma
  policy IAM inline (`InvocarClaudeOpus45`) aplicada por CLI à role do Runtime.
- **Repasse do token ao container.** O Runtime usa
  `request_header_configuration` com `Authorization` na allowlist, para o
  container chamar o Gateway com o JWT do usuário.
- **Imagem do agente.** Publicada no ECR (`dev-maia-agentes`); a cada mudança do
  container, o ponteiro de imagem do Runtime é atualizado por
  `cdk deploy MAIA-dev-Agents -c imagem_agentes=<uri>` (preserva env e headers).

## Metodologia e fontes

Verificação feita em **fevereiro de 2026** consultando a documentação oficial
da AWS (CloudFormation Template Reference, referência do CDK v2 e o developer
guide do Bedrock AgentCore). As fontes estão listadas ao final. Onde há
incerteza, está sinalizado explicitamente.

## Suporte no CloudFormation (`AWS::BedrockAgentCore::*`)

A família de recursos existe e cobre os quatro pilares citados na tarefa. O
marco inicial (Runtime + Memory + tagging + CloudFormation) foi anunciado nas
release notes do AgentCore em **setembro de 2025**; desde então a cobertura foi
ampliada para Gateway, targets e identidade. Recursos relevantes para este
projeto:

| Pilar (design) | Recursos CloudFormation | Observações |
|---|---|---|
| **Gateway** | `AWS::BedrockAgentCore::Gateway`, `GatewayTarget`, `GatewayRule`, `GatewayRateLimit` | `AuthorizerType: CUSTOM_JWT` habilita a validação de JWT do Cognito na entrada; `WafConfiguration` e `KmsKeyArn` suportados; `GatewayTarget` roteia para tools (Lambda, OpenAPI, Smithy, MCP) |
| **Runtime** | `AWS::BedrockAgentCore::Runtime`, `RuntimeEndpoint` | Runtime serverless baseado em contêiner; suporta `CustomJWTAuthorizerConfiguration` e VPC (set./2025); referencia uma imagem de contêiner (ECR) |
| **Identity** | `AWS::BedrockAgentCore::WorkloadIdentity`, `OAuth2CredentialProvider`, `ApiKeyCredentialProvider`, `ResourcePolicy` | Não há um recurso único chamado "Identity"; o isolamento por agente é composto por WorkloadIdentity, provedores de credencial (Token Vault), `ResourcePolicy` e as roles IAM/escopos do authorizer |
| **Memory** | `AWS::BedrockAgentCore::Memory` | Suporta `EncryptionKeyArn` (KMS) e estratégias de memória (short-term por expiração de eventos e long-term por estratégias gerenciadas) |

Referência-índice de todos os tipos: `AWS_BedrockAgentCore` no CloudFormation
Template Reference (ver fontes).

## Suporte no CDK (`aws_cdk.aws_bedrockagentcore`)

O módulo está presente no `aws-cdk-lib` v2 e expõe duas camadas:

- **L1 (`Cfn*`)** — cobertura 1:1 com o CloudFormation: `CfnGateway`,
  `CfnGatewayTarget`, `CfnRuntime`, `CfnRuntimeEndpoint`, `CfnMemory`,
  `CfnWorkloadIdentity`, `CfnOAuth2CredentialProvider`,
  `CfnApiKeyCredentialProvider`, `CfnResourcePolicy`, entre outros.
- **L2 (construct de alto nível)** — em evolução: `Gateway`, `GatewayTarget`,
  `Memory`, `WorkloadIdentity`, `ApiKeyCredentialProvider`, além de fábricas
  úteis como `GatewayAuthorizer`/`CustomJwtAuthorizer` (JWT do Cognito),
  `GatewayCredentialProvider`, `ManagedMemoryStrategy` e configurações de
  target Lambda (`LambdaTargetConfiguration`).

> **Atenção à versão do CDK.** O `infra/requirements.txt` fixa
> `aws-cdk-lib>=2.177.0`, piso anterior ao lançamento do suporte a AgentCore
> (set./2025). O módulo `aws_bedrockagentcore` aparece nas versões recentes do
> CDK (a documentação consultada referencia v2.25x+). **Antes da 9.2 será
> necessário elevar o piso do `aws-cdk-lib`** para uma versão que exponha o
> módulo e, de preferência, os L2 desejados. A versão exata do primeiro release
> com o módulo não foi confirmada nesta pesquisa; validar com
> `pip index versions aws-cdk-lib` / changelog do CDK ao subir o piso.

## O que é provisionável via IaC (na `AgentsStack`)

Estes recursos entram na `infra/stacks/agents_stack.py` via CDK (L2 quando
disponível, senão L1 `Cfn*`):

1. **Gateway (entrada + tools routing)** — `Gateway` com
   `AuthorizerType = CUSTOM_JWT` apontando para o User Pool do Cognito
   (`SecurityStack`); `WafConfiguration` e `KmsKeyArn` opcionais.
2. **Tools routing** — um `GatewayTarget` por tool: as 6 Lambdas da
   `ComputeStack` e a Knowledge Base da `KnowledgeBaseStack`.
3. **Memory** — `Memory` com `EncryptionKeyArn` (chave KMS da `SecurityStack`),
   estratégia short-term (TTL 7–365 dias) e long-term.
4. **Identidade/credenciais** — `WorkloadIdentity` e provedores de credencial
   necessários para o outbound auth das tools; `ResourcePolicy` quando houver
   acesso cross-account/serviço.
5. **Runtime + RuntimeEndpoint** — o ambiente de execução que hospeda a imagem
   dos agentes. O *recurso* é declarável por IaC; a *imagem* referenciada não.

## O que exige AWS CLI/console (fora do IaC declarativo)

1. **Imagem de contêiner dos agentes** — build da lógica de orquestração
   sequencial dos 5 agentes (framework como Strands/LangGraph/CrewAI) e push
   para o ECR. É um artefato de build; será feito por script CLI idempotente.
   O `Runtime` (IaC) apenas referencia a URI/tag da imagem já publicada.
2. **Habilitação de acesso ao modelo** — liberar o modelo
   `openai.gpt-oss-120b-1:0` no Bedrock (model access) é uma operação de conta,
   feita no console/CLI, não modelada em IaC.
3. **Ingestão do Knowledge Base** — já coberta pelo `scripts/ingerir_kb.py`
   (tarefa 8.3), disparada após o deploy.
4. **Ajustes finos de políticas por agente** — refinamentos de escopos/claims
   do authorizer ou do policy engine que dependam de identificadores gerados no
   deploy podem ser aplicados via CLI de forma idempotente.

## Plano recomendado

- **`AgentsStack` (CDK)** contém tudo que é declarável: Gateway (+ authorizer
  JWT do Cognito), GatewayTargets (6 Lambdas + KB), Memory, WorkloadIdentity/
  credenciais, Runtime e RuntimeEndpoint. Enquanto a imagem do agente não
  existir, o `Runtime` fica como *placeholder documentado* (ou condicionado a um
  parâmetro/contexto com a URI da imagem), evitando falha de synth/deploy.
- **Scripts CLI idempotentes em `scripts/`** cuidam do que não é declarativo:
  `configurar_agentcore.py` (planejado) para build/push da imagem e wiring
  dependente de artefatos, seguindo o padrão idempotente de `ingerir_kb.py`
  (comparar estado atual antes de agir; reexecução segura).
- **Passos de alto nível do script CLI planejado:**
  1. `aws bedrock ...` — confirmar/registrar acesso ao modelo GPT OSS 120B.
  2. Build da imagem do agente e push para o ECR (idempotente por tag/digest).
  3. Atualizar o `Runtime` (via IaC re-deploy com a nova URI, ou
     `aws bedrock-agentcore-control update-agent-runtime` quando aplicável).
  4. Validar o endpoint do Runtime e o roteamento do Gateway ponta a ponta.

## Impacto nas tarefas seguintes

- **9.2 (Gateway + tools routing):** implementável via CDK (`Gateway` +
  `GatewayTarget`), com authorizer `CUSTOM_JWT` do Cognito. Requer elevar o piso
  do `aws-cdk-lib`.
- **9.3 (Identity / isolamento por agente):** via CDK usando `WorkloadIdentity`,
  provedores de credencial e roles IAM/escopos do authorizer (a matriz de tools
  do design vira políticas/targets por agente). Sem recurso único "Identity".
- **9.5 (Memory):** via CDK (`Memory`) com short/long-term e KMS.
- **9.6 (registrar os 5 agentes no Runtime):** parte IaC (`Runtime`/
  `RuntimeEndpoint`) + parte CLI (imagem do agente e orquestração). A adição de
  novos agentes não altera os existentes (atende ao Requisito 13.1).

## Pendências antes da 9.2

1. Elevar e fixar o piso do `aws-cdk-lib` para uma versão que exponha
   `aws_bedrockagentcore` (confirmar a versão mínima no changelog do CDK).
2. Confirmar disponibilidade de `AWS::BedrockAgentCore::*` em `us-east-1`.
3. Decidir o framework de orquestração da imagem do agente (ex.: Strands).

## Fontes

- CloudFormation Template Reference — índice `AWS_BedrockAgentCore` e recursos
  `Gateway`, `GatewayTarget`, `Runtime`, `RuntimeEndpoint`, `Memory`,
  `WorkloadIdentity`, `ResourcePolicy`
  (`https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/AWS_BedrockAgentCore.html`).
- Referência do CDK v2 — módulo `aws_cdk.aws_bedrockagentcore`
  (`https://docs.aws.amazon.com/cdk/api/v2/python/aws_cdk.aws_bedrockagentcore.html`).
- Amazon Bedrock AgentCore — Release notes (set./2025: Runtime/Memory + suporte
  a CloudFormation e tagging)
  (`https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/release-notes.html`).
- Amazon Bedrock AgentCore Developer Guide (Gateway, Memory, Workload Identity).

_Conteúdo reescrito e resumido a partir das fontes para conformidade com
restrições de licenciamento. Consulta realizada em fevereiro de 2026; como o
AgentCore evolui rápido, revalidar as versões antes do deploy._
