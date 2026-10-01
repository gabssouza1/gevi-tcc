# Talent Card — TCC GEVI

Textos prontos para uso em LinkedIn, portfolio, currículo e talent cards de
recrutamento. Todos descrevem o mesmo projeto (o GEVI) em profundidades e
formatos diferentes. Escolha a versão que caiba no espaço disponível.

## Versão curta (~50 palavras — headline de talent card ou perfil)

Construí, sozinho, um sistema multiagente de recomendação de investimentos
sobre Amazon Bedrock AgentCore: 3 agentes especialistas (Risco, Seleção
de Ativos, Explicador) coordenados por um orquestrador, com AgentCore
Gateway (autorização JWT), Memory (preferências e fatos por usuário),
Knowledge Base (RAG) e Bedrock Converse API. Reduzi a latência de
resposta em ~43% (de ~123s para ~70s).

## Versão média (~180 palavras — descrição de projeto no LinkedIn/CV)

**GEVI — Sistema Multiagente de Recomendação de Investimentos (TCC)**

Projeto individual de fim de graduação. Sistema web onde o investidor
conversa com uma assistente de IA (ClaraInvest) que analisa perfil,
carteira e cenário econômico para recomendar rebalanceamentos. Arquitetura
serverless de ponta a ponta na AWS.

Backend em Python com um orquestrador coordenando três agentes
especialistas: Risco (simulação de volatilidade), Seleção de Ativos
(análise de portfólio e ranking de ativos) e Explicador (consolidação em
linguagem natural adaptada ao usuário). Modelo dual — Claude Opus para
orquestração e redação final, Claude Haiku para notas internas dos
agentes especialistas.

Frontend em Next.js/TypeScript com Cognito para autenticação, hospedado
em S3 + CloudFront. Infraestrutura provisionada como código em AWS CDK.

Otimizei a latência do pipeline em ~43% (123s → 70s) via paralelismo de
tool-calls na Converse API, tools diretas para leituras simples e modelo
dual. Adicionei feedback de progresso incremental via polling no DynamoDB
para eliminar o "vazio" percebido durante a resposta.

## Versão longa (descrição de portfolio ou preparação de entrevista)

### O produto

GEVI é um sistema multiagente de recomendação de investimentos entregue
como plataforma web. O usuário faz onboarding, cadastra a carteira
(ativos, valores, tipos), e conversa em português com a assistente
ClaraInvest — uma interface conversacional que analisa perfil de risco,
cenário econômico (Selic, IPCA, dólar, CDI) e composição da carteira
para sugerir rebalanceamentos.

### A arquitetura

Sistema 100% serverless na AWS, projetado com privacidade por design
(cada usuário isolado por identidade do Cognito, propagada como JWT até
cada ferramenta).

- **Frontend**: Next.js 14 (export estático) + TypeScript + Tailwind,
  hospedado em S3 privado atrás de CloudFront com Origin Access Control,
  WAF e HTTPS obrigatório.
- **Autenticação**: Amazon Cognito com fluxo de cadastro, confirmação
  por e-mail, MFA opcional e questionário de suitability.
- **API**: Amazon API Gateway (REST) → Lambda proxy (Python) que valida
  o JWT do Cognito e roteia para o AgentCore ou para tools diretas.
- **Camada de agentes**: Amazon Bedrock AgentCore Runtime (container
  serverless) executa um orquestrador coordenando três agentes
  especialistas via loop de tool-use da Converse API:
  - Orquestrador (Claude Opus) — decide quais especialistas acionar
  - Agente_Risco (Claude Haiku) — simulação de volatilidade
  - Agente_Selecao_Ativos (Claude Haiku) — análise e ranking
  - Agente_Explicador / XAI (Claude Opus) — consolida achados e adapta
    a linguagem ao nível do usuário
- **Ferramentas**: AgentCore Gateway (protocolo MCP) roteia chamadas
  para seis Lambdas de tools (perfil, indicadores, análise de
  portfólio, simulação de risco, seleção de ativos, integração com
  APIs externas via EventBridge).
- **Memória**: AgentCore Memory com estratégias de curto prazo
  (turnos da conversa) e longo prazo (fatos e preferências do
  investidor entre sessões).
- **RAG**: Amazon Bedrock Knowledge Base com OpenSearch Serverless para
  conteúdo educativo, consultado pelo Explicador quando o usuário é
  iniciante.
- **Dados**: DynamoDB para carteiras, conversas, jobs assíncronos e
  usuários; S3 para documentos da KB.
- **IaC**: 100% em AWS CDK (Python), inclusive as camadas de
  segurança (KMS, roles IAM com least-privilege, CloudTrail).

### Decisões técnicas que fiz

**Redução de latência de ~43%** (123s → 70s):

- Diagnóstico via CloudWatch — instrumentei cada chamada ao LLM com
  duração e stopReason antes de otimizar.
- Paralelizei tool-calls independentes no mesmo turno (`stopReason:
  tool_use` com múltiplos blocos executados em ThreadPoolExecutor).
- Reduzi de 5 para 3 agentes especialistas: Perfil e Macroeconômico
  viraram tools diretas do orquestrador — não faziam raciocínio, só
  liam dados persistidos, pagar o custo de um LLM não se justificava.
- Modelo dual — Opus para orquestração/redação final (qualidade
  importa), Haiku para agentes que só produzem notas internas
  (velocidade importa).
- Consultas ao Knowledge Base em lote (múltiplas paralelas no mesmo
  turno, em vez de sequenciais).
- Atalho no orquestrador: quando o Explicador retorna, uso a resposta
  dele direto como final em vez de fazer mais uma chamada ao LLM só
  para "confirmar".

**Feedback de progresso incremental** (latência percebida):

- Como a chamada ao Runtime é HTTP síncrona (sem streaming), o Runtime
  grava a etapa atual do pipeline direto no DynamoDB durante a
  execução, e o polling do frontend mostra "Analisando sua
  carteira...", "Preparando explicação...". Usuário para de olhar para
  um spinner mudo por 1-2 minutos.

**Determinismo por API, não por prompt**:

- Descobri (durante debugging) que instruir o modelo por prompt para
  chamar uma tool obrigatória não é garantia — o modelo às vezes
  responde direto da memória de longo prazo. Corrigi com
  `toolConfig.toolChoice` da Converse API, que é uma restrição
  estrutural no espaço de resposta do modelo. Lição documentada: quando
  comportamento é crítico, forçar pela API, não pelo prompt.

**Correções de bugs com causa raiz documentada**:

- Conversas de chat "ressuscitando" depois de excluídas — root cause:
  `update_item` do DynamoDB sem `ConditionExpression` recria itens que
  não existem. Corrigi adicionando `attribute_exists`.
- "0,00% vs. mês anterior" nos indicadores — root cause: o cálculo
  usava o segundo item mais recente da tabela local (que a cada 5
  minutos é gravado com o mesmo valor pelo EventBridge), em vez do
  `valor_anterior` real da série do BCB já persistido no item.
- Composição de carteira desatualizada nas respostas — root cause: LLM
  respondendo da memória de longo prazo sem consultar a tool.
  Corrigido com `toolChoice` forçado disparado por heurística de
  palavra-chave.

### Testes e qualidade

- 77 testes automatizados: property-based tests com Hypothesis para
  invariantes do design (isolamento de tools por agente, somente-
  leitura das Lambdas de indicadores, sinal coerente da variação
  percentual), testes de integração com mocks da Converse API, testes
  de pipeline.
- Cobertura das propriedades numeradas do design (Propriedade 6, 10,
  etc. — comentários no código com `# Req X.Y` como rastreabilidade
  requisito↔teste).

### Documentação técnica

Quatro documentos consolidados (`sistema/docs/`): decisões de agentes
e latência, decisões de usabilidade (com bugs e trade-offs), defesa
das ferramentas AWS, defesa metodológica (Kiro + AI-DLC como
referência).

## Stack técnica (checklist rápido)

**Linguagens**: Python 3.12, TypeScript, JavaScript

**Frontend**: Next.js 14, React, Tailwind CSS, AWS Amplify (auth)

**Backend**: AWS Lambda, Python (boto3), Model Context Protocol (MCP)

**AI/LLM**: Amazon Bedrock (Claude Opus 4.5, Claude Haiku), Bedrock
Converse API com tool use, inference profiles cross-region

**Plataforma de agentes**: Amazon Bedrock AgentCore Runtime, Gateway
(MCP), Memory (short-term e long-term), Knowledge Base (RAG),
WorkloadIdentity

**Infraestrutura**: AWS CDK (Python), DynamoDB, S3, CloudFront,
CloudTrail, API Gateway, EventBridge, KMS, IAM, WAF, Cognito,
OpenSearch Serverless

**DevOps / práticas**: IaC end-to-end, deploy scripts idempotentes,
observabilidade via CloudWatch, feature flags implícitos via
inference profiles

**Metodologia**: AI-DLC (AWS ProServe) como framework de referência,
Kiro IDE (specs, steering, hooks) como ambiente de desenvolvimento,
HITL em pontos críticos, ADRs implícitos na documentação

**Testes**: pytest, Hypothesis (property-based testing), moto (mock AWS)

## Habilidades demonstradas

- **Arquitetura de sistemas multiagentes**: desenho de responsabilidades,
  isolamento de tools por agente, decisões de "quando um agente é
  necessário e quando é só uma tool"
- **Otimização de desempenho baseada em medição**: diagnóstico via
  logs, identificação de gargalos reais antes de otimizar, aplicação de
  paralelismo onde faz sentido, escolha de modelo por tarefa
- **Segurança AWS**: least-privilege em roles IAM, autenticação
  end-to-end (Cognito JWT → API Gateway → Lambda → Gateway → Tool),
  criptografia em trânsito e em repouso (KMS), separação de escopo por
  usuário (namespace na Memory, chave composta no DynamoDB)
- **Debugging de sistemas de IA**: distinção entre bug de código, bug
  de prompt, bug de dado; uso de `toolChoice` como restrição estrutural
- **Comunicação técnica escrita**: documentação de decisões com
  contexto/opções/consequências, comentários no código explicando
  *por que*, não *o quê*
- **Autonomia com HITL responsável**: capacidade de trabalhar com
  agentes de IA sem perder controle sobre decisões críticas (deploys,
  mudanças de infraestrutura, edição de prompts que afetam produção)

## Adaptação para inglês (opcional)

Se precisar de versão em inglês do headline (~50 palavras):

> Solo-built a multi-agent investment recommendation system on Amazon
> Bedrock AgentCore: 3 specialist agents (Risk, Asset Selection,
> Explainer) coordinated by an orchestrator, with AgentCore Gateway
> (JWT auth), Memory (per-user preferences and facts), Knowledge Base
> (RAG) and the Bedrock Converse API. Reduced response latency by ~43%
> (from ~123s to ~70s).

Se precisar de mais texto em inglês, use este como semente e ajuste o
resto do documento.
