# Defesa Metodológica: Kiro IDE e AI-DLC

Este documento justifica a escolha do **Kiro** como IDE de desenvolvimento
e da metodologia **AI-DLC (AI Development Lifecycle)** como framework de
referência de processo do GEVI. Serve como referência para a defesa do
TCC, ao lado dos documentos de decisões técnicas
([`decisoes-agentes-e-latencia.md`](decisoes-agentes-e-latencia.md)),
usabilidade ([`decisoes-usabilidade.md`](decisoes-usabilidade.md)) e defesa
das ferramentas AWS ([`defesa-agentcore-bedrock.md`](defesa-agentcore-bedrock.md)).

Enquanto os documentos irmãos respondem *o que foi construído e por quê*,
este responde **como foi construído** — o ambiente de desenvolvimento e o
framework de referência que orientou a organização do trabalho.

## Por que essa discussão importa

Sistemas multiagentes têm uma característica que os diferencia de sistemas
tradicionais: a complexidade do produto é **dispersa entre agentes,
prompts, tools e fluxos de decisão** — não concentrada em módulos de
código lidos linearmente. Isso muda o que é "manutenível":

- Um bug pode estar num prompt (linguagem natural), não em código.
- Uma feature nova pode ser um novo agente + novas tools, não uma classe
  nova num módulo existente.
- A "correção" de comportamento pode exigir editar contexto/steering,
  não editar código.

Fazer isso sem um ambiente que trate esses artefatos como cidadãos de
primeira classe leva a prompts espalhados sem versionamento, decisões de
design perdidas em conversas com LLMs que não persistem, e retrabalho
constante porque cada iteração começa sem contexto. O GEVI foi
desenvolvido no **Kiro**, cujas primitivas cobrem exatamente esses
artefatos, com o **AI-DLC** servindo de vocabulário para pensar sobre as
fases do trabalho.

## 1. Kiro: o IDE agentic da Amazon

### O que é

Kiro é um IDE construído sobre o VS Code, desenvolvido pela Amazon,
projetado para desenvolvimento assistido por agentes de IA. Diferente de
plugins de IA em IDEs tradicionais (Copilot no VS Code, chat lateral no
Cursor), o Kiro trata o agente como **primeira classe** — ele participa
da estrutura do projeto e do fluxo de trabalho, não é um recurso lateral.

### Como o Kiro foi usado no GEVI

O uso concreto do Kiro no desenvolvimento do GEVI foi principalmente no
**loop de implementação**: escrever código, executar testes, iterar sobre
comportamento, corrigir bugs. As primitivas do Kiro que mais pesaram:

**Spec** — em `.kiro/specs/multiagent-investment-advisor/` vive o
documento estruturado do sistema (requirements, design, tasks). Ele foi
escrito e revisado manualmente pelo autor, mas viveu no formato do Kiro
porque isso permite que o agente do IDE tenha o contexto do projeto
inteiro carregado a cada sessão, sem precisar re-explicar o que é o
sistema a cada nova conversa.

**Steering files** — arquivos em `.kiro/steering/` são injetados
automaticamente no contexto de cada interação. Servem como "constituição"
do projeto: convenções de código, práticas de segurança, workflow de
colaboração. Como o TCC durou meses e teve dezenas de sessões de trabalho,
sem esses arquivos cada retomada partiria do zero em termos de
convenções.

**Hooks** — em `.kiro/hooks/` residem automações que reagem a eventos do
IDE (salvamento, criação de arquivo, submit de prompt). Usadas para
lembretes de linting, testes de regressão e verificações antes de
commits.

**MCP (Model Context Protocol)** — Kiro suporta servidores MCP
nativamente. Isso permitiu integrar documentação AWS oficial, pesquisa
técnica interna e o próprio Bedrock AgentCore Gateway como fontes de
contexto durante o desenvolvimento — sem copiar/colar entre janelas de
browser.

**Autonomia configurável**:
- **Autopilot** — agente executa autonomamente, usuário revisa em bloco.
  Usado para tarefas com escopo claro (renomear, aplicar patches
  mecânicos, gerar código a partir de spec já aprovado).
- **Supervised** — agente pede aprovação a cada mudança. Usado em
  operações de risco alto (mudanças de infraestrutura AWS, edições em
  código que atende usuários reais, deploys).

### Por que Kiro em vez de alternativas

| Ferramenta | O que oferece | Por que não bastou para o GEVI |
|---|---|---|
| **VS Code + Copilot** | Autocomplete inline, chat lateral | Sem estrutura de spec, sem steering persistente entre sessões, sem hooks integrados |
| **Cursor** | Chat inline, edições multi-arquivo, `.cursorrules` | `.cursorrules` cobre parte do papel do steering, mas não há primitiva de spec estruturado nem framework nativo de sub-agentes |
| **Claude Code (CLI)** | Agente terminal potente, contextos amplos | Excelente para tarefas pontuais, mas fora do fluxo do IDE — sem primitivas de spec/hook integradas |
| **Kiro** | Spec + steering + hooks + MCP como primitivas nativas | Ambiente completo, alinhado com o vocabulário do AI-DLC |

Para um projeto do porte do GEVI (~15 componentes distintos entre
backend, frontend, IaC e agentes), o overhead de configurar cada
primitiva manualmente em outra ferramenta se pagaria negativamente no
prazo do TCC.

## 2. AI-DLC: metodologia de referência

### O que é

AI-DLC (AI Development Lifecycle) é o framework metodológico da AWS
ProServe para engajamentos de desenvolvimento assistido por IA. Estrutura
o trabalho em **quatro milestones sequenciais** (M1–M4), com gates de
qualidade entre eles.

- **M1 — Align & Configure**: assessment de maturidade, seleção do use
  case, configuração da plataforma, definição de métricas baseline,
  plano de OCM (Organizational Change Management).
- **M2 — Prove Value**: execução completa do workflow em um use case
  selecionado, com HITL (Human-In-The-Loop) nos pontos críticos.
- **M3 — Adapt & Integrate**: adaptação aos padrões organizacionais,
  integração com CI/CD existente, capacitação de champions internos.
- **M4 — Scale & Handover**: transferência de ownership, medição final
  de impacto, posicionamento de trabalhos futuros.

### Sete métricas AI-DLC canônicas

O método rastreia sete métricas ao longo do ciclo:

1. **AI Input Quality Score** — qualidade dos requisitos e specs
2. **AI Artifact Acceptance Rate** — aceite de artefatos gerados por IA
   em code review
3. **Rework Rate** — retrabalho pós-merge dentro de N dias
4. **Requirements-to-Outcome Traceability** — rastreabilidade
   requisito ↔ código ↔ métrica de negócio
5. **Decision-Outcome Correlation** — decisões arquiteturais vs.
   incidentes
6. **AI Amplification Factor** — throughput com IA vs. baseline
7. **Feature Adoption Delta** — adoção das features entregues

### AI Operating Model

Componente central do AI-DLC: **explicita quais decisões o agente pode
tomar autonomamente e quais exigem revisão humana**. Não é sobre limites
técnicos do modelo — é sobre governança:

- **Autônomas**: refatorações mecânicas, geração de código de baixo
  risco, formatação, testes unitários simples.
- **Requer HITL**: mudanças de arquitetura, edição de prompts que afetam
  produção, código de segurança.
- **Human-reserved**: aprovações de deploy em produção, trade-offs
  estratégicos, decisões com implicações de negócio.

### Como o AI-DLC foi usado no GEVI

Importante ser honesto: o AI-DLC foi **consultado como referência de
método**, não seguido stricto sensu — o framework foi originalmente
desenhado para engajamentos consultivos de 12+ semanas com equipes de
6+ pessoas, com sponsor executivo, workshops multi-persona e assessments
formais de maturidade. Um TCC individual com prazo comprimido não
comporta essas atividades no formato canônico.

O que foi adotado do AI-DLC:

- **Vocabulário de fases (M1–M4)** para organizar mentalmente o trabalho
  — o que é "definir o problema" (M1), "provar que funciona" (M2),
  "polir para uso real" (M3), "empacotar para o futuro" (M4).
- **Princípio de HITL em pontos críticos** — cada mudança de prompt que
  afeta comportamento do modelo em produção passou por revisão manual e
  teste antes de deploy. Cada mudança de IAM/infraestrutura foi
  confirmada explicitamente antes de aplicar. As trocas de conversa
  neste TCC contêm dezenas de "posso aplicar?" que atestam isso.
- **Rastreabilidade requisito ↔ código** — comentários com `# Req X.Y` em
  vários pontos do backend referenciam propriedades do design, e a
  suíte de testes de propriedade (`tests/test_prop*.py`) valida
  invariantes numeradas do design.
- **AI Operating Model implícito** — nunca escrito em documento
  formal, mas praticado consistentemente: mudanças de produção sempre
  pediram confirmação, decisões arquiteturais sempre passaram por
  análise de trade-off documentada (ver ADRs implícitos nos outros
  três docs desta pasta).

O que **não** foi adotado do AI-DLC:

- Assessment formal de maturidade multi-persona (não faz sentido para
  um autor individual)
- Workshop com sponsor executivo (o autor foi simultaneamente sponsor,
  product owner, arquiteto e desenvolvedor)
- Métricas 3, 5 e 6 da lista canônica não foram medidas formalmente por
  falta de baseline pré-projeto para comparar
- OCM (Organizational Change Management) com champion enablement e
  rollout planejado — só há um usuário operacional (o autor) e um
  usuário-teste (o orientador/avaliador)

Essa transparência é a base da defesa: o método foi *aplicado no
espírito*, não *reproduzido na forma*.

## 3. Mapeamento das quatro fases ao desenvolvimento real

Aplicando o vocabulário do AI-DLC ao GEVI, na escala compatível com um
TCC individual:

### M1 (equivalente) — Definição

Antes de escrever código, o autor definiu:

- **Spec do sistema** — o que o GEVI faz, para quem, com quais
  requisitos funcionais e não-funcionais. Documento vivo em
  `.kiro/specs/multiagent-investment-advisor/` que evoluiu ao longo do
  projeto.
- **Arquitetura de alto nível** — os componentes principais (frontend
  Next.js, Cognito, API Gateway, Lambda proxy, AgentCore Runtime,
  Gateway, Memory, Knowledge Base, Bedrock, DynamoDB) e como se
  conectam. Consolidada em `design.md` do spec.
- **Métricas de sucesso** — latência de resposta e cobertura de testes
  como métricas quantitativas principais. Bugs de percepção do usuário
  como métrica qualitativa (nº de correções aplicadas com causa raiz
  documentada).
- **Plataforma AWS** — Bedrock model access, Cognito user pool, tabelas
  DynamoDB, buckets S3, roles IAM iniciais provisionados via CDK.

### M2 (equivalente) — Implementação e prova de valor

Construção incremental do pipeline completo até chegar em resposta
end-to-end funcionando:

- Lambdas de tools (perfil de investidor, indicadores econômicos,
  análise de portfólio, simulação de risco, seleção de ativos, consulta
  de APIs externas)
- Container do AgentCore Runtime com a lógica dos agentes
- Frontend Next.js com onboarding, dashboard, chat, investimentos,
  configurações
- Testes: 77 testes cobrindo propriedades do design, integração e
  invariantes de segurança (isolamento de tools por agente, somente-
  leitura de Lambdas de indicadores)

**Iterações significativas nessa fase** (todas com HITL — nenhuma
aplicada sem revisão):

1. De agente único → 6 agentes → 4 agentes (redução por análise de
   trade-off, ver `decisoes-agentes-e-latencia.md`)
2. Modelo dual (Opus para redação final, Haiku para notas internas)
3. Paralelização de tool-calls no mesmo turno da Converse API
4. Correção do bug de conversa "ressuscitada" via `ConditionExpression`
5. Correção do bug de "0,00% vs mês anterior" na Lambda de indicadores
6. Correção do bug de "IA responde dados obsoletos" via `toolChoice`
   forçado

### M3 (equivalente) — Integração e polimento

Adaptação do sistema às convenções do projeto e polimento de UX para
uso real:

- **Steering files consolidados** em `.kiro/steering/` cobrindo
  práticas AWS, padrões de segurança, workflow de commits, guidelines
  de escrita técnica.
- **Deploy scripts** (`scripts/configurar_agentcore.py`,
  `aws lambda update-function-code`, `aws s3 sync`, invalidação
  CloudFront) — reproduzíveis mas não formalizados em CodePipeline
  (fora do escopo do TCC).
- **Iterações de UX** com feedback real do orientador/usuário —
  10 iterações documentadas em `decisoes-usabilidade.md`, cada uma com
  problema identificado, análise de causa, correção aplicada e
  verificação em produção.
- **Feedback incremental do chat** (nível 1) — o Runtime grava a etapa
  atual do pipeline no DynamoDB durante o processamento, e o polling
  do frontend mostra "Analisando sua carteira...", "Preparando a
  explicação...", em vez de spinner mudo por 1-2 minutos.

### M4 (equivalente) — Empacotamento

Preparação para "vida após o TCC":

- **Documentação técnica** desta pasta (`sistema/docs/`) — quatro
  documentos cobrindo os quatro eixos de defesa (agentes/latência,
  usabilidade, AgentCore/Bedrock, Kiro/AI-DLC).
- **Preparação para GitHub público** — identificação de dados
  sensíveis, ajuste de exemplos com placeholders, `.gitignore` com
  todas as categorias de secret.
- **Botão de suporte** em `/suporte` para receber feedback de usuários
  do repositório público.
- **Métricas finais de impacto** (foco em latência, o principal
  requisito não-funcional):
  - Latência de resposta: **123s → ~70s** (redução ~43%)
  - Latência percebida: **silêncio total → 4 estados intermediários**
    visíveis ao usuário durante o processamento
  - Bugs de percepção corrigidos: **6 casos** com causa raiz e
    prevenção documentadas
- **Trabalhos futuros identificados**: streaming real de tokens
  (nível 2), histórico completo de carteira em tabela dedicada,
  formalização do AI Operating Model se o projeto crescer para
  múltiplos desenvolvedores.

## 4. Mapeamento Kiro ↔ AI-DLC

Não é acaso que o Kiro tenha primitivas alinhadas com o AI-DLC — o IDE
foi construído pela Amazon consciente da metodologia da ProServe. Cada
primitiva do Kiro tem um artefato correspondente no vocabulário do
AI-DLC:

| Primitiva do Kiro | Artefato AI-DLC equivalente | Como foi usado no GEVI |
|---|---|---|
| `.kiro/specs/<nome>/` | Requirements + Design + Task Plan | Spec `multiagent-investment-advisor` como fonte única do que o sistema faz |
| `.kiro/steering/*.md` | AI Operating Model + Skill Packages | Steering files cobrindo padrões AWS, segurança, workflow, escrita |
| `.kiro/hooks/*.json` | Pipeline Integration Points | Automação de linting, testes de regressão, verificação de commits |
| Sessão Vibe | Discovery / conversas exploratórias | Refinamento incremental de requisitos e design |
| Sessão Spec | Execução guiada pelo spec | Implementação de tarefas na ordem do plano |
| Autopilot mode | Tasks autônomas do AI Operating Model | Refatorações mecânicas, geração de testes |
| Supervised mode | HITL checkpoints | Mudanças de prompt, infraestrutura, deploys |
| MCP servers | External tool integrations | Documentação AWS, pesquisa técnica, integração com AgentCore |

Esse mapeamento operacionaliza a metodologia: sem uma primitiva de spec,
tarefas viram lista de TODOs desestruturados; sem steering persistente,
cada sessão começa "esquecendo" as convenções do projeto; sem hooks
integrados, verificações automáticas viram checklist manual esquecível.

## 5. Evidências concretas no repositório

O uso do Kiro e a aplicação de princípios AI-DLC deixaram traços
verificáveis:

**Rastreabilidade requisito ↔ código**:

- `# Req 7.3` em `fn-consulta-indicadores/handler.py`
- `# Propriedade 6` (isolamento de tools por agente) em
  `test_prop06_e_pipeline.py`
- `# Propriedade 10` (somente-leitura) em
  `test_prop10_consulta_indicadores.py`
- `# tarefa 9.6` em `agentcore/servidor.py`

**ADRs (Architecture Decision Records) implícitos** distribuídos na
documentação:

- Decisão de reduzir de 6 para 4 agentes reais
  (`decisoes-agentes-e-latencia.md` §2, §4)
- Decisão de usar `toolChoice` em vez de reforçar prompt textualmente
  (`decisoes-agentes-e-latencia.md` §8)
- Decisão de manter histórico de carteira "simples" (só última mudança)
  em vez de tabela dedicada (`decisoes-usabilidade.md` §4)
- Decisão de não implementar streaming real por enquanto
  (`decisoes-agentes-e-latencia.md` §7)

Cada ADR segue o padrão AI-DLC: contexto, opções consideradas, decisão
tomada, consequências aceitas.

**HITL checkpoints reais**: cada bug identificado e corrigido
(6 casos em `decisoes-usabilidade.md` §10) representa um checkpoint
onde comportamento produzido pelo sistema foi rejeitado, análise de
causa raiz foi feita, correção foi aplicada e a prevenção documentada.
Isso é o AI Operating Model funcionando na prática.

**Métricas verificáveis**: cada afirmação numérica neste corpo
documental (123s → 70s, 77 testes, 6 bugs corrigidos, 10 iterações de
UX) é rastreável ao código, aos logs do CloudWatch ou ao histórico da
conversa de desenvolvimento. Nenhuma "métrica cosmética".

## 6. Trade-offs assumidos

Ser honesto sobre limites do que foi feito:

- **Escopo comprimido**: um engajamento AI-DLC completo (variant
  Standard) leva 12 semanas com equipe de 6+ pessoas. O TCC comprimiu
  as atividades em escala 1 pessoa × prazo de TCC. Muitas atividades
  canônicas do M1 (assessment multi-persona, workshop com sponsor,
  seleção de use case entre alternativas) foram implícitas ou
  substancialmente reduzidas.
- **Nem todas as 7 métricas AI-DLC foram implementadas**. Foco em
  latência e nas métricas derivadas (qualidade percebida via bugs
  corrigidos, retrabalho por bugs de percepção). "AI Artifact Acceptance
  Rate" e "AI Amplification Factor" não foram medidos formalmente — sem
  baseline pré-projeto para comparar. Trabalho futuro: rastrear
  taxa de aceite/edição de código gerado pelo agente numa próxima
  iteração.
- **OCM (Organizational Change Management) trivial**: só há um usuário
  operacional (o autor) e um usuário-teste. O componente de OCM que
  domina engajamentos comerciais reais (champion enablement,
  comunicação org-wide, planejamento de rollout) foi reduzido a
  "documentação clara para leitores futuros".
- **AI Operating Model implícito**: nunca formalmente escrito em
  documento próprio. Formalizar seria útil se o GEVI evoluir de TCC
  para produto com múltiplos desenvolvedores — hoje o modelo vive
  na disciplina do autor e nos steering files.
- **Dependência de ferramenta em evolução**: Kiro é um IDE
  relativamente novo. Mudanças de API/comportamento entre versões
  poderiam impactar o projeto. Mitigação: os artefatos importantes
  (specs, steering, hooks) são arquivos texto versionados; se o Kiro
  fosse descontinuado, esses arquivos continuariam legíveis por
  qualquer ferramenta que suporte formatos similares (ex.: Cursor com
  `.cursorrules`, Claude Code com `CLAUDE.md`).

## 7. Por que essa combinação faz sentido para o TCC

Um TCC em Ciência da Computação avaliado em 2026 precisa demonstrar não
só que o autor sabe programar, mas que **entende as práticas
contemporâneas** de construção de software com IA. A combinação
Kiro + AI-DLC:

- **Kiro como IDE**: mostra domínio de ferramentas de última geração,
  não só de editores tradicionais + copilotos genéricos.
- **AI-DLC como framework de referência**: mostra que o desenvolvimento
  teve estrutura, não foi improvisado — as fases têm nome, os
  princípios são reconhecidos, as decisões estão rastreáveis.

Do ponto de vista da banca:

- **Reprodutibilidade**: outro estudante com o mesmo Kiro, mesmos
  specs, mesmos steering files, produziria um sistema muito similar.
  Isso é o oposto de "o aluno teve uma inspiração pontual" — é
  engenharia com registro.
- **Rastreabilidade**: cada decisão importante está registrada em
  código ou em documentação (ADRs implícitos), o que permite revisar
  raciocínios anos depois sem precisar reconstruir a partir do vazio.
- **Ampliabilidade**: se o GEVI virasse produto real, o próximo
  desenvolvedor pegaria o projeto sem "arqueologia" — Kiro carregaria
  automaticamente steering e specs, e o AI-DLC daria o vocabulário
  para conversar sobre o roadmap.

## Referências

- **AI-DLC Engagement Guide** (interno ProServe)
- **awslabs/aidlc-workflows** — implementação de referência dos
  workflows AI-DLC no GitHub
- **aws-samples/sample-long-running-app-harness** — harness de agentes
  autônomos em Bedrock AgentCore
- **aws-samples/sample-autonomous-cloud-coding-agents** — padrões de
  agentes autônomos de código na AWS
- Documentação oficial do Kiro em https://kiro.dev/

## Onde isso vive no repositório

- `.kiro/specs/multiagent-investment-advisor/` — spec principal
  (requirements + design + tasks)
- `.kiro/steering/*.md` — artefatos do AI Operating Model (padrões,
  segurança, workflow)
- `.kiro/hooks/*.json` — hooks de automação
- `sistema/docs/` — documentação de decisões (este e outros três docs)
- `sistema/tests/test_prop*.py` — property tests referenciando
  propriedades numeradas do design (rastreabilidade requisito↔teste)
- Comentários no código com `# Req X.Y` — traceability matrix implícita
