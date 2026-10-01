# Defesa Técnica: Amazon Bedrock e AgentCore

Este documento justifica a escolha de **Amazon Bedrock** (LLM gerenciado) e
**Amazon Bedrock AgentCore** (plataforma de agentes conversacionais) como
base do sistema multiagente do GEVI, em vez de alternativas mais
tradicionais como containers próprios em ECS/EKS + biblioteca de agentes
(LangChain, Strands, CrewAI) + serviço de embeddings à parte.

Serve como referência para a defesa do TCC — explica *o que ganhamos* e
*o que aceitamos como trade-off* em cada camada.

## Contexto do problema

O GEVI é um sistema multiagente de recomendação de investimentos. Cada
requisição do usuário envolve:

1. Ler perfil de risco + dados cadastrais (Cognito + DynamoDB)
2. Ler carteira atual do usuário (DynamoDB)
3. Consultar indicadores macroeconômicos atualizados (DynamoDB)
4. Simular risco da carteira (cálculo determinístico + LLM)
5. Selecionar/ranquear ativos compatíveis com o perfil (cálculo + LLM)
6. Consultar base de conhecimento educativa quando o usuário é iniciante
   (RAG)
7. Consolidar todos os achados numa resposta em linguagem natural
   adaptada ao nível do usuário (LLM)
8. Persistir a conversa e extrair fatos/preferências para a próxima
   interação (memória de longo prazo)

Cada passo tem sua natureza (leitura, cálculo, LLM), sua tolerância a
falhas, e seus requisitos de segurança (auth do usuário tem que chegar
até cada tool). Esta documentação explica por que a AWS foi escolhida
para cada uma dessas camadas.

## 1. Amazon Bedrock — o LLM gerenciado

### O que é

Serviço gerenciado da AWS que expõe modelos de linguagem de vários
provedores (Anthropic Claude, Amazon Nova, Meta Llama, Cohere, AI21,
Mistral) através de uma única API. Não roda no cliente — a chamada sai
da nossa conta AWS para o serviço, o modelo executa em infraestrutura
gerenciada, a resposta volta.

### Por que Bedrock em vez de chamar Anthropic direto (ou OpenAI)

- **Auth por IAM, não por chave de API**: cada Lambda/container que
  chama o modelo usa a role IAM que já tem. Não há segredo compartilhado
  para rotacionar, nem chave em variável de ambiente para vazar. Se um
  container é comprometido, restringir o acesso ao modelo é uma linha na
  policy — não precisa regenerar credencial em nenhum outro lugar.
- **Modelos co-localizados com o resto da infra**: latência rede
  cliente↔modelo é intra-AWS (~5–50ms), não WAN (~100–300ms) como seria
  chamando um provedor externo. Para um pipeline que faz 4–6 chamadas
  ao LLM por requisição, esse delta se acumula rapidamente.
- **Auditoria via CloudTrail**: toda chamada ao modelo fica registrada
  automaticamente. Para um sistema financeiro que ainda deve suportar
  auditoria de recomendações, essa é uma camada gratuita.
- **Data residency previsível**: os dados do prompt/resposta não saem da
  região configurada (ou do escopo do inference profile cross-region que
  escolhemos, cujas regiões são explícitas). Fora da AWS, o operador do
  modelo pode processar os dados em regiões geográficas não
  compartilhadas com o resto da nossa infra.

### Converse API: o "tool use" nativo que economizou implementação

A Bedrock Converse API oferece uma abstração unificada para chamadas com
tool use — o modelo recebe uma lista de ferramentas disponíveis e pode
retornar `stopReason: tool_use` com blocos `toolUse` que descrevem qual
tool ele quer chamar e com quais argumentos.

Isso importa porque:

- **Não temos que fazer parsing de "function calls" em texto livre**. Em
  APIs sem tool use nativo, o LLM retornaria algo como `Vou chamar
  analisar_portfolio(user_id=...)`, e o cliente teria que fazer parsing
  frágil disso — regex, fine-tuning específico, retentativas para
  formato inválido. A Converse API entrega o toolUse estruturado
  (`{"name": "...", "input": {...}}`).
- **Chamadas paralelas no mesmo turno funcionam nativamente**. O modelo
  pode retornar N blocos `toolUse` no mesmo turno; nós executamos em
  paralelo com `ThreadPoolExecutor` (ver `_executar_usos_em_paralelo`
  em `agentcore/agentes.py`). Sem essa capacidade nativa, teríamos que
  encadear cada chamada, com o custo de latência descrito em detalhe em
  `decisoes-agentes-e-latencia.md` seção 1.
- **`toolChoice` como restrição estrutural**. O parâmetro
  `toolConfig.toolChoice` aceita `{"tool": {"name": "..."}}` para forçar
  a chamada de uma tool específica. Usamos isso em produção para corrigir
  um bug crítico onde o modelo respondia dados obsoletos da memória de
  longo prazo em vez de consultar a fonte real. Prompts diziam para
  "obrigatoriamente chamar a tool antes de responder" — o modelo
  ignorava. `toolChoice` é uma decisão estrutural no espaço de resposta
  do modelo: ele *não pode* responder sem chamar. Isso não teria sido
  possível numa API que só aceita "system prompt + user message".

### Modelo dual: Opus + Haiku pela mesma API

Como a Converse API abstrai o modelo por `modelId`, o mesmo código de
tool-use funciona sem alteração para diferentes modelos. Isso permitiu a
otimização de custo/latência descrita em `decisoes-agentes-e-latencia.md`
seção 4 (Claude Opus para orquestração e redação final, Claude Haiku
para agentes que só produzem notas internas). Cada agente carrega o
próprio `modelId`, propagado pela `_loop_tool_use` até `_chamar_modelo`.
Sem essa uniformidade de API, teríamos dois "clientes" no código, cada
um com seu formato de tool use, sua contagem de tokens, sua forma de
lidar com `stopReason` — a duplicação inviabilizaria trocar de modelo
por agente.

### Inference profiles cross-region

Modelos como o Claude Opus 4.5 são oferecidos como **inference
profile** — não uma região específica, mas um "profile" que roteia a
chamada dinamicamente entre múltiplas regiões (us-east-1, us-east-2,
us-west-2) conforme capacidade. Isso dá alta disponibilidade sem
precisar implementar failover no cliente. A permissão IAM lista o ARN
do profile *e* os ARNs dos foundation models em cada uma das regiões
possíveis:

```python
recursos_modelos = [
    f"arn:aws:bedrock:us-east-1:{account}:inference-profile/{modelo}"
    for modelo in (MODELO_LLM, MODELO_LLM_RAPIDO)
] + [
    f"arn:aws:bedrock:{regiao}::foundation-model/{modelo.removeprefix('us.')}"
    for modelo in (...) for regiao in _REGIOES_INFERENCE_PROFILE
]
```

Ver `infra/stacks/agents_stack.py: _criar_role_runtime`.

## 2. AgentCore Runtime — hospedagem serverless dos agentes

### O que é

Serviço da família AgentCore que executa o container do agente em modo
serverless. Contrato HTTP simples: `GET /ping` (health check) e
`POST /invocations` (executa uma interação). Escalonamento automático,
sessões isoladas por usuário via header
`X-Amzn-Bedrock-AgentCore-Runtime-Session-Id`, timeout gerenciado.

### Por que Runtime em vez de ECS/EKS/Lambda

Alternativas consideradas:

**Lambda** (função HTTP direta): rejeitada pelo teto de 15 minutos de
execução, insuficiente para o pior caso do pipeline atual (~1-2 min
hoje, mas prompts mais elaborados ou modelos mais lentos poderiam
exceder). Também: cold start impactaria latência percebida em picos.

**ECS Fargate + ALB**: viável, mas exige gerenciar deployment
(task definitions, target groups, health checks), custo mínimo de tarefa
sempre ligada mesmo sem tráfego, e complexidade extra para injetar o JWT
do usuário como identidade de sessão até o modelo.

**AgentCore Runtime**: escala a partir do zero, cobra por uso real,
integra nativamente com o resto do AgentCore (Gateway, Memory,
Identity), e o formato de sessão (`Runtime-Session-Id`) já é o que a
Memory usa como `sessionId`. O container é o mesmo Docker
padrão — mudou só onde ele é executado, não como é construído.

### Trade-off: endpoints com versionamento próprio

Documentado em `decisoes-agentes-e-latencia.md` seção 9. Cada endpoint
nomeado do Runtime tem uma `liveVersion` própria e não segue
automaticamente a versão mais recente do Runtime. Isso é *feature*
(permite deploy blue/green por endpoint, ex.: `dev`, `staging`, `prod`
apontando para versões diferentes ao mesmo tempo) mas é *gotcha* na
operação (é preciso atualizar o endpoint explicitamente depois de
atualizar o Runtime).

Trade-off aceito: essa mecânica dá controle fino que outras plataformas
não dão gratuitamente. O custo é documentação e disciplina de deploy.

## 3. AgentCore Gateway — proxy de tools com autenticação JWT

### O que é

O Gateway roteia chamadas a "tools" (Lambdas, APIs OpenAPI, MCP servers)
sob um único endpoint MCP (Model Context Protocol). Faz a autorização
de cada chamada validando o JWT do usuário conforme o
`CustomJWTAuthorizerConfiguration` (para o GEVI: JWT emitido pelo pool
Cognito do usuário logado).

### Por que Gateway em vez de "cada tool ser uma Lambda separada"

Sem Gateway, o container do agente teria que:

1. Guardar credencial IAM para invocar cada Lambda diretamente (⇒
   ampliação do escopo de permissões do container além do estritamente
   necessário).
2. Validar o JWT do usuário em cada Lambda-tool (⇒ código repetido em
   cada função, com risco de inconsistência).
3. Expor um endpoint próprio para cada tool (⇒ N URIs, N configurações
   de autorização, N pontos de manutenção).

Com Gateway:

- **Auth centralizada**: o Gateway valida o JWT uma vez, na entrada. Se
  o token não bate com o pool Cognito configurado, a requisição nem
  chega à tool. Cada Lambda-tool recebe uma identidade já validada.
- **Um único endpoint** (`/mcp`) para o container falar com o mundo
  externo — o container só precisa de acesso *ao Gateway*, não a cada
  Lambda separadamente.
- **Rate limiting e WAF** configuráveis no Gateway em vez de por
  Lambda.
- **MCP como protocolo** de tools significa que a mesma tool pode ser
  usada por outros clientes MCP no futuro (ex.: Claude Desktop, outros
  agentes) sem código adicional.

### O `Authorization` header do usuário chega até a tool

A Lambda proxy (fn-api-proxy) invoca o Runtime passando o JWT do usuário
no header `Authorization`. O Runtime, dentro do container, passa esse
mesmo header adiante em cada chamada MCP ao Gateway (ver
`_criar_chamador_tool` em `agentcore/servidor.py`). O Gateway valida,
resolve para a Lambda alvo (nome real da tool: `<target>___<nome>`, ex.:
`gerenciar_perfil_investidor___gerenciar_perfil_investidor`), e essa
Lambda recebe o `user_id` extraído do JWT — ou seja, cada tool sabe
*quem* está pedindo, sem que o container do agente possa se passar por
outro usuário.

Isso é o que garante o **isolamento por usuário** no back — o modelo
não pode "combinar" com o container para acessar carteira de outra pessoa.

## 4. AgentCore Memory — memória curta e longa gerenciada

### O que é

Serviço gerenciado que armazena eventos de conversa (`create_event`,
turnos usuário↔IA) e aplica *estratégias* para extrair fatos e
preferências de longo prazo em background, disponibilizados via
`retrieve_memory_records` com busca semântica.

### Duas dimensões, dois casos de uso

- **Curto prazo** (últimos turnos da mesma conversa): recarregado a cada
  invocação com `list_events`. Serve para o modelo manter o fio do
  diálogo sem receber a conversa inteira toda vez. Escopado por
  `actorId=user_id, sessionId=conversaId`.
- **Longo prazo** (preferências e fatos): dois namespaces gerenciados
  pela Memory (`/investidor/<user_id>/preferencias` e
  `/investidor/<user_id>/fatos`). Extração é assíncrona (rodada pela
  Memory em background, com o LLM da estratégia). Recuperação é busca
  semântica dentro do namespace do usuário.

### Por que Memory gerenciada em vez de implementar do zero

Alternativa: guardar tudo em DynamoDB e implementar a extração de fatos
via cron + LLM. Considerações:

- **Isolamento por namespace nativo**: cada usuário tem seu próprio
  namespace na Memory; não há risco de vazamento cross-user por bug de
  filtro no cliente. Se o `user_id` no namespace é errado, a chamada
  simplesmente falha.
- **Extração de fatos como estratégia gerenciada**: definimos a
  estratégia declarativamente (via IaC ou CLI); a Memory roda a extração
  no ritmo dela, retentando falhas, sem exigir cron/queue do nosso lado.
- **Menos código de estado que a gente escreve**: `list_events`,
  `create_event`, `retrieve_memory_records` são chamadas simples. Sem a
  Memory, teríamos que projetar o schema, indexar por relevância
  semântica (⇒ embeddings, OpenSearch), garantir consistência
  eventual, testar migrações de schema, etc.

### Trade-off e lição operacional

A memória de longo prazo pode ficar **desatualizada** e o modelo
tratar como verdade (ver bug real corrigido em
`decisoes-agentes-e-latencia.md` seção 8). O aprendizado: memória
gerenciada não isenta de considerar que dados podem estar velhos. Para
dados que exigem precisão em tempo real (composição de carteira), a
solução foi forçar a consulta à tool sempre que a mensagem contém
palavras-chave que indicam pedido desses dados. A Memory é ótima para
"contexto" (preferências, histórico), ruim para "verdade autoritativa".

## 5. Amazon Bedrock Knowledge Base — RAG gerenciado

### O que é

Serviço que ingere documentos (PDF, HTML, TXT, Markdown), os fragmenta,
gera embeddings, indexa num vetor DB (OpenSearch Serverless ou outro), e
expõe uma API `retrieve` que faz busca semântica. Tudo é gerenciado; o
código do agente só precisa chamar `Retrieve(knowledgeBaseId, query)` e
receber os trechos mais relevantes.

### Por que KB em vez de fazer RAG manual

Fazer RAG manualmente exigiria:

- Escolher e provisionar um vetor DB (OpenSearch Serverless / pgvector
  no Aurora / Pinecone / etc.)
- Pipeline de ingestão (baixar/parsear PDFs, chunk, embed, indexar)
- API de busca própria (chunk retrieval + re-ranking + formatação)
- Monitoramento e re-ingestão quando docs mudam

Com Knowledge Base do Bedrock:

- **OpenSearch Serverless** provisionado como back-end (só configurar,
  não operar)
- **Ingestão automática** de um bucket S3 configurado; ao adicionar/
  remover doc no bucket, a KB re-indexa
- **API única** (`bedrock-agent-runtime.retrieve`) com `retrievalQuery`
  aceitando texto natural
- **Escala com o volume**: se a base de conhecimento crescer de 10 pra
  10.000 documentos, o serviço acompanha sem reconfigurar

### Uso no GEVI

Só o Agente_Explicador tem acesso ao KB
(ver `AGENTES_ESPECIALISTAS["Agente_Explicador"]["tools"]` em
`agentcore/agentes.py`). Ele consulta o KB quando o nível do usuário é
"basico", para trazer conteúdo educativo complementar sobre 2–3
conceitos mencionados na resposta. Detalhes de otimização (múltiplas
consultas paralelas no mesmo turno) em `decisoes-agentes-e-latencia.md`
seção 5.


## 6. AgentCore Identity — workload identity

### O que é

Componente do AgentCore que emite identidades (`WorkloadIdentity`) para o
Runtime chamar outros serviços do AgentCore (Gateway, Memory) sem
credenciais estáticas. O Runtime recebe automaticamente uma identidade
válida no token
`X-Amzn-Bedrock-AgentCore-Runtime-Workload-Accesstoken`, que os outros
serviços validam.

### Ganho de segurança

Sem WorkloadIdentity, teríamos que:

- Criar credencial IAM ou API key para o container do agente falar com o
  Gateway/Memory
- Rotacionar essa credencial periodicamente
- Gerenciar risco de vazamento (a credencial estaria em variável de
  ambiente do container)

Com WorkloadIdentity, a autenticação entre componentes do AgentCore é
mediada pela plataforma. Não há segredo compartilhado, portanto não há
segredo para rotacionar nem vazar.

## 7. Comparação sintética com uma alternativa "faça em casa"

| Camada | Solução AWS (adotada) | "Faça em casa" (alternativa considerada) |
|---|---|---|
| LLM | Bedrock + Converse API | Chamar Anthropic/OpenAI direto |
| Auth de tool | Gateway com JWT authorizer nativo | Lambda authorizer custom em API Gateway |
| Memory | AgentCore Memory | DynamoDB + cron + OpenSearch |
| RAG | Bedrock Knowledge Base | OpenSearch/pgvector + pipeline manual |
| Hospedagem agente | AgentCore Runtime | ECS Fargate atrás de ALB |
| Identidade service-to-service | WorkloadIdentity | Credenciais IAM ou chaves rotacionadas |
| Multi-modelo | Um `modelId` diferente por chamada | Dois clientes SDK diferentes |
| Cross-region failover | Inference profile | Failover manual no cliente |

Cada uma dessas escolhas isoladamente pode ser questionada; a soma
delas é o que torna o GEVI viável dentro do escopo de um TCC — sem
essa base, o esforço de infraestrutura consumiria o esforço de
produto.

## Trade-offs consolidados

Todo esse pacote traz custos que precisam ser reconhecidos honestamente
na defesa:

- **Vendor lock-in AWS**: migrar o sistema para outra nuvem ou para
  auto-hospedagem exigiria reescrever a autenticação (auth JWT do
  Gateway, WorkloadIdentity), reimplementar Memory e KB do zero, e
  substituir Bedrock por outro provedor de LLM. Não é um pequeno
  refactor; é uma reescrita substancial da camada de agentes.
- **Custo variável**: cobrança por tokens (Bedrock), por invocação
  (Runtime), por armazenamento vetorial (KB), por evento (Memory). Sob
  carga baixa é ótimo; em produção massiva exige monitoramento
  cuidadoso.
- **Curva de aprendizado**: AgentCore é relativamente novo (algumas
  partes ainda em preview durante o desenvolvimento deste TCC).
  Detalhes operacionais como o versionamento de endpoints não estão
  bem documentados e foram descobertos pela via difícil (ver
  seção 9 em `decisoes-agentes-e-latencia.md`).
- **Dependência de disponibilidade AWS**: se o Bedrock estiver
  indisponível em todas as regiões do inference profile ou o AgentCore
  ficar degradado, o sistema é impactado em bloco. Não há fallback
  local implementado (nem seria trivial — o LLM é a única fonte de
  raciocínio no pipeline).

Nenhum desses trade-offs foi surpresa; todos foram aceitos como custo
razoável para o objetivo do TCC (mostrar sistema multiagente real em
produção com ferramentas modernas), e podem ser revisitados se o GEVI
evoluir de projeto acadêmico para produto operacional.

## Onde isso vive no código

- `infra/stacks/agents_stack.py`: define Gateway, Runtime, Memory,
  WorkloadIdentity, IAM roles com permissões escopadas para cada
  modelo/KB/Memory
- `infra/stacks/knowledge_base_stack.py`: KB + OpenSearch Serverless
- `agentcore/servidor.py`: chamador da Converse API
  (`_chamar_modelo`), integração com Gateway (`_criar_chamador_tool`),
  Memory (`_carregar_historico`, `_salvar_turno`), KB (`_recuperar_kb`)
- `agentcore/agentes.py`: lógica dos agentes, uso do `toolChoice`,
  paralelismo de tool-calls
- `lambdas/fn-*`: tools individuais expostas pelo Gateway
- `scripts/configurar_agentcore.py`: build/push da imagem para ECR
- `infra/AGENTCORE_IAC.md`: registro do que pode ser IaC e o que ainda
  exige CLI/console no AgentCore

## Referências para a defesa

Documentos irmãos que aprofundam pontos específicos:

- `decisoes-agentes-e-latencia.md` — como a latência foi diagnosticada
  e reduzida de ~123s para ~70s; feedback de progresso incremental;
  `toolChoice` como restrição estrutural
- `decisoes-usabilidade.md` — melhorias de UX/UI, correção de bugs de
  percepção, princípios orientadores de design

Documentação AWS relevante consultada durante o projeto:

- [AgentCore Runtime — release notes](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/release-notes.html)
- [Bedrock Converse API — tool use](https://docs.aws.amazon.com/bedrock/latest/userguide/tool-use.html)
- [Bedrock Knowledge Bases — overview](https://docs.aws.amazon.com/bedrock/latest/userguide/knowledge-base.html)
- [AgentCore Gateway — MCP protocol](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html)
- [Inference profiles cross-region](https://docs.aws.amazon.com/bedrock/latest/userguide/inference-profiles.html)
