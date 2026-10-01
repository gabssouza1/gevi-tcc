# Roteiro de Apresentação — TCC GEVI

Roteiro pronto para uma apresentação de ~15–20 minutos cobrindo: problema,
arquitetura, protótipo, o que já foi feito, o que ainda falta. Use os
títulos como slides e as notas como script mental do que dizer.

Ao final: perguntas prováveis da banca com respostas curtas prontas.

---

## Estrutura sugerida (12 slides, ~15–18 min + 5–10 min de perguntas)

| # | Slide | Tempo |
|---|---|---|
| 1 | Capa | 30s |
| 2 | O problema | 1min30 |
| 3 | Solução proposta | 1min30 |
| 4 | Arquitetura — visão geral | 2min |
| 5 | Arquitetura — camada de agentes | 2min |
| 6 | Stack técnica | 1min |
| 7 | Demonstração (protótipo) | 3–4min |
| 8 | O que já foi feito — resultados | 3min |
| 9 | Decisões técnicas importantes | 2min |
| 10 | Métricas de impacto | 1min |
| 11 | Trabalhos futuros | 1min30 |
| 12 | Considerações finais | 30s |

---

## Slide 1 — Capa

**No slide**:

- Título: GEVI — Sistema Multiagente de Recomendação de Investimentos
- Subtítulo: com Explicabilidade e Inteligência Artificial Generativa
- Seu nome
- Orientador
- Curso, instituição, data

**O que falar**:

> "Bom dia/tarde. Vou apresentar o GEVI, um sistema web multiagente de
> recomendação de investimentos que construí como TCC, usando Amazon
> Bedrock AgentCore e IA generativa. Em ~15 minutos vou passar pelo
> problema, arquitetura, uma demonstração rápida, o que já entreguei e
> o que fica como trabalho futuro."

---

## Slide 2 — O problema

**No slide**:

- Investidores pessoa física crescem no Brasil (mostrar número se
  souber), mas assessoria personalizada é cara e escassa
- Recomendações prontas na internet ignoram o **perfil individual**
- Ferramentas de IA generalistas (ChatGPT, etc.) não têm contexto da
  carteira nem cenário econômico atualizado
- Falta **explicabilidade**: "por que esse ativo é bom para MIM?"

**O que falar**:

> "O investidor pessoa física no Brasil cresceu muito nos últimos anos,
> mas ainda esbarra em três limitações: assessoria personalizada é
> cara, recomendações genéricas na internet ignoram o perfil dele, e
> ferramentas de IA generalistas não têm contexto da carteira ou do
> cenário econômico atual. Além disso, quando uma IA responde 'invista
> em X', o usuário não sabe *por quê* — falta explicabilidade. O GEVI
> ataca esses quatro pontos."

---

## Slide 3 — Solução proposta

**No slide**:

- Plataforma web onde o investidor conversa com uma assistente de IA
  ("ClaraInvest")
- A IA analisa **perfil**, **carteira real** e **cenário econômico
  atualizado** antes de responder
- **Explicabilidade (XAI)**: cada recomendação vem com o raciocínio
- Sistema **multiagente**: cada agente é especialista em uma coisa —
  Risco, Seleção de Ativos, Explicação
- Adaptação de linguagem ao **nível do usuário** (básico vs. avançado)

**O que falar**:

> "A proposta é uma plataforma onde o usuário conversa em português
> com a ClaraInvest, que consulta o perfil dele, a carteira real e os
> indicadores do BCB antes de responder. Por baixo, não é um único
> modelo grande respondendo tudo — são agentes especialistas que se
> coordenam: um faz simulação de risco, outro analisa portfólio e
> ranqueia ativos, um terceiro consolida os achados e adapta a
> linguagem ao nível do usuário. É esse último agente que garante a
> explicabilidade — a resposta explica *por que* está sugerindo cada
> coisa."

---

## Slide 4 — Arquitetura (visão geral)

**No slide**: diagrama de alto nível

```
[ Usuário / Browser ]
        ↓
[ CloudFront + S3 (frontend Next.js) ]
        ↓
[ Cognito (auth) + API Gateway ]
        ↓
[ Lambda Proxy ] ──→ [ DynamoDB (portfólios, conversas, jobs) ]
        ↓
[ AgentCore Runtime (Orquestrador + 3 especialistas) ]
     ↙     ↓      ↘
[ Gateway ]  [ Memory ]  [ Knowledge Base ]
     ↓          ↓             ↓
[ 6 Lambdas ]  [DynamoDB]  [ OpenSearch ]
   (tools)     (mem+longo)   (RAG)
     ↓
[ Bedrock Converse API (Claude Opus + Haiku) ]
```

**O que falar** (dedo apontando no fluxo):

> "O usuário acessa o frontend estático hospedado em S3 e CloudFront.
> Autentica no Cognito. Toda chamada da UI passa pelo API Gateway e
> chega numa Lambda proxy que valida o JWT e roteia. Se for uma leitura
> simples — perfil, carteira — vai direto no DynamoDB. Se for uma
> pergunta pro chat, vira um job assíncrono e dispara o AgentCore
> Runtime, onde vivem os agentes. Os agentes usam três coisas: Gateway,
> que dá acesso às ferramentas do sistema; Memory, que guarda o
> histórico e as preferências; e Knowledge Base, para conteúdo
> educativo. Tudo executando modelos Claude na Amazon Bedrock via
> Converse API."

---

## Slide 5 — Arquitetura (camada de agentes)

**No slide**:

- **Orquestrador** (Claude Opus): decide o fluxo
- **3 Especialistas**:
  - **Agente_Risco** (Haiku): simulação de volatilidade
  - **Agente_Selecao_Ativos** (Haiku): análise + ranking
  - **Agente_Explicador / XAI** (Opus): consolidação em linguagem
    natural adaptada
- **2 tools diretas** do Orquestrador (sem raciocínio):
  - Perfil do investidor
  - Indicadores econômicos
- Modelo dual: Opus onde qualidade importa, Haiku onde velocidade
  importa
- Paralelismo: múltiplas tool-calls no mesmo turno

**O que falar**:

> "A camada de agentes tem quatro entidades LLM: um orquestrador que
> decide o fluxo, e três especialistas que ele aciona. O orquestrador
> também tem acesso a duas ferramentas diretas — perfil e indicadores
> — que não exigem raciocínio, só leem dados do banco. Isso foi uma
> decisão consciente: se a tarefa é ler um dado, não vale a pena pagar
> o custo de mais um LLM. Uso modelo dual: Claude Opus para o
> orquestrador e o explicador, onde qualidade da resposta importa; e
> Claude Haiku para os agentes de risco e seleção, que só produzem
> notas internas concisas. Isso reduz custo e latência sem perder
> qualidade percebida."

---

## Slide 6 — Stack técnica

**No slide** (logos ou texto):

- **Frontend**: Next.js 14, TypeScript, Tailwind, AWS Amplify (auth)
- **Backend**: Python 3.12, AWS Lambda, boto3
- **IA**: Amazon Bedrock (Claude Opus 4.5 + Haiku), Converse API
- **Plataforma de agentes**: Amazon Bedrock AgentCore (Runtime +
  Gateway + Memory + Knowledge Base + WorkloadIdentity)
- **Dados**: DynamoDB, S3, OpenSearch Serverless
- **Segurança**: Cognito, WAF, KMS, IAM (least-privilege)
- **IaC**: AWS CDK (Python) — 100% em código
- **Testes**: pytest + Hypothesis (property-based testing), 77 testes

**O que falar**:

> "Stack 100% na AWS, tudo serverless, tudo provisionado como código
> em CDK Python. Isso significa que quem clonar o repositório consegue
> subir um ambiente igual ao meu com dois comandos. Frontend em
> Next.js, backend em Python. Testes automatizados incluem
> property-based testing com Hypothesis para invariantes do design,
> como 'nenhuma Lambda de leitura pode escrever no DynamoDB'."

---

## Slide 7 — Demonstração do protótipo

**No slide**: capturas das principais telas (ou faça demo ao vivo, se
o ambiente estiver estável)

Sugestão de fluxo se for demo ao vivo (~3–4 min):

1. **Login** — mostra o Cognito, JWT, isolamento por usuário
2. **Dashboard** — resumo da carteira (valor + perfil + última
   alteração), gráfico de pizza de alocação, listagem por categoria,
   indicadores do BCB
3. **Investimentos** — cadastro de ativos com alocação viva
4. **Chat com a ClaraInvest** — enviar "faça uma recomendação
   completa" e mostrar o progresso incremental ("Consultando perfil…",
   "Analisando carteira…", "Preparando explicação…"). Ao terminar,
   mostrar a resposta com XAI, disclaimer, formatação.
5. **Minha Conta** — cartão de resumo (nome + perfil + valor) e
   perfil do investidor

**O que falar** (se for demo ao vivo):

> "Vou entrar rapidamente. Aqui no dashboard vocês veem o resumo da
> minha carteira: R$ 18.000 em três ativos, perfil moderado, última
> alteração há N dias. O gráfico de pizza mostra a alocação por tipo
> — hoje 44% em FII, 33% em renda fixa e 22% em renda variável. Vou
> pedir uma recomendação completa para a ClaraInvest… reparem que ela
> não fica em silêncio: mostra em qual etapa está no pipeline. Isso
> reduz o 'medo do spinner mudo'."

**Backup se a demo falhar**: screenshots já no slide, pra você seguir
a apresentação sem depender de rede/conta.

---

## Slide 8 — O que já foi feito

**No slide** (formato de checklist visual):

- ✓ Arquitetura serverless completa em AWS
- ✓ 4 entidades LLM (orquestrador + 3 especialistas)
- ✓ 6 ferramentas MCP via AgentCore Gateway (com autorização JWT)
- ✓ Frontend responsivo em Next.js com onboarding, dashboard, chat,
  investimentos, minha conta, configurações, suporte
- ✓ 77 testes automatizados
- ✓ Redução de latência de ~43% (123s → 70s)
- ✓ Feedback de progresso incremental (elimina o vazio de 1–2 min)
- ✓ 6 bugs identificados e corrigidos com causa raiz documentada
- ✓ Documentação técnica: 5 documentos cobrindo decisões e defesa

**O que falar**:

> "Todos os itens dessa lista estão funcionando em produção agora, com
> ambiente dev acessível pela URL. Não é maquete — é sistema real,
> escalável, com segurança em camadas. Destaco três coisas que vou
> aprofundar no próximo slide: a otimização de latência, o feedback
> incremental e a estratégia de decisões técnicas que documentei."

---

## Slide 9 — Decisões técnicas importantes

**No slide** (três destaques):

### 1. Otimização de latência ~43% (123s → 70s)

- Diagnóstico via CloudWatch (não otimizei no chute)
- Paralelismo de tool-calls no mesmo turno da Converse API
- Redução de 6 → 4 agentes (Perfil e Macro viraram tools diretas)
- Modelo dual: Opus onde importa, Haiku onde não
- KB em lote (múltiplas consultas paralelas)

### 2. Feedback de progresso incremental

- Chamada ao Runtime é HTTP síncrona (sem streaming nativo)
- Solução: Runtime grava a etapa atual no DynamoDB durante o
  processamento; frontend faz polling e mostra
- Latência real não muda, mas a **percebida** cai drasticamente

### 3. Determinismo por API, não por prompt

- Bug encontrado: modelo respondia com dados obsoletos da memória
  mesmo com instrução "obrigatória" no prompt
- Correção: `toolConfig.toolChoice` da Converse API — força a chamada
  da tool como restrição estrutural
- Lição: prompts são "sugestões elaboradas"; garantia vem da API

**O que falar**:

> "Três decisões técnicas que valem destacar. Primeiro, a latência: eu
> medi antes de otimizar. Instrumentei o código para logar duração de
> cada chamada ao modelo, e descobri que dois gargalos consumiam a
> maioria do tempo — chamadas sequenciais que podiam ser paralelas, e
> agentes que não faziam raciocínio, só liam dados. Corrigi os dois e
> economizei ~53 segundos.
>
> Segundo, o feedback incremental. A chamada ao Runtime da AWS é HTTP
> síncrona — não tem streaming nativo. Em vez de reclamar, eu resolvi
> lateralmente: o Runtime grava a etapa atual do processamento no
> DynamoDB, e o frontend, que já faz polling para saber quando a
> resposta ficou pronta, agora também mostra a etapa. Não é streaming
> real, mas o usuário vê 4 estados intermediários em vez de silêncio.
>
> Terceiro — e essa é uma lição interessante — descobri debugando que
> instrução por prompt não é garantia de comportamento. O modelo,
> mesmo com uma regra 'obrigatória' no prompt, respondia com valores
> antigos da memória sem consultar a ferramenta que traria o dado
> real. Corrigi com `toolChoice` da Converse API, que é uma restrição
> estrutural: o modelo *não pode* responder sem chamar a ferramenta.
> A lição que documentei: quando o comportamento é crítico, força
> pela API, não pelo prompt."

---

## Slide 10 — Métricas de impacto

**No slide** (números grandes, formato de dashboard):

- **Latência de resposta**: 123s → 70s (**-43%**)
- **Latência percebida**: silêncio → **4 estados** visíveis
- **Bugs corrigidos**: **6 casos** com causa raiz documentada
- **Testes automatizados**: **77 passando**
- **Iterações de UX**: **10 iterações** com feedback do usuário
- **Componentes na AWS**: **~15** provisionados via CDK

**O que falar**:

> "Números concretos: latência caiu 43%, seis bugs importantes foram
> corrigidos com análise de causa raiz (não fiquei tratando sintoma),
> 77 testes automatizados garantem que essas correções não regressam,
> e dez iterações de UX foram aplicadas com base em feedback real. Não
> é 'trabalhei bastante' — é 'estes são os efeitos mensuráveis'."

---

## Slide 11 — Trabalhos futuros

**No slide** (agrupados por categoria):

### Curto prazo (implementável no atual)

- **Streaming real de tokens** (nível 2): trocar API Gateway REST por
  Function URL com response streaming; usuário vê a resposta aparecer
  palavra por palavra, como ChatGPT
- **Histórico completo de carteira** em tabela dedicada
  (`PortfolioHistorico`), com timeline navegável
- **Análise textual da ClaraInvest no perfil do usuário** — sugestão
  personalizada de rebalanceamento na tela de conta

### Médio prazo (evolução do produto)

- Integração com corretora real para leitura automática da carteira
  (hoje é cadastro manual)
- Alertas proativos por e-mail quando indicador ou ativo cruza um
  limiar (parte da infraestrutura já existe: EventBridge + tabela de
  limiares no perfil)
- Suporte a mais classes de ativo (ETFs internacionais, criptomoedas
  com preço real de API)

### Governança / metodologia

- Formalizar o **AI Operating Model** em documento próprio, se o
  projeto crescer para múltiplos desenvolvedores
- Implementar as métricas AI-DLC restantes (AI Amplification Factor,
  AI Artifact Acceptance Rate) para medir formalmente o impacto do
  desenvolvimento assistido por IA
- Deploy em produção com CI/CD formal (CodePipeline em vez de
  scripts idempotentes)

**O que falar**:

> "O que fica para depois se divide em três blocos. Curto prazo:
> streaming real de tokens, que exige trocar o API Gateway por
> Function URL — decidi não fazer agora porque cabia na versão atual
> uma solução menos invasiva. Histórico completo de carteira, que
> deixei simples deliberadamente para não expandir o escopo. E uma
> análise textual da ClaraInvest personalizada na tela de perfil.
>
> Médio prazo: integrar com corretora real, alertas proativos por
> e-mail, mais classes de ativo. E, do lado de governança e método,
> formalizar o AI Operating Model se o projeto crescer, e medir as
> métricas de metodologia que ficaram fora do escopo do TCC."

---

## Slide 12 — Considerações finais

**No slide**:

- **O que o GEVI mostra**: viável construir sistema multiagente
  completo, com IA generativa, sobre serviços AWS de última geração
- **Por trás disso**: método (AI-DLC como referência), ambiente (Kiro
  como IDE), decisões documentadas (5 documentos técnicos)
- **Repositório**: [github.com/seu-usuario/gevi](https://github.com/)
  (público)

**O que falar**:

> "Concluindo: o GEVI mostra que dá para construir um sistema
> multiagente real, com IA generativa, sobre a stack mais recente da
> AWS, dentro do escopo de um TCC individual. Mais do que o produto
> funcionando, o que fica documentado são as decisões técnicas —
> latência, arquitetura, correções de bugs, escolha de ferramentas —
> tudo com causa e efeito rastreáveis. O repositório é público, e a
> documentação completa está lá. Obrigado, estou aberto para
> perguntas."

---

## Perguntas prováveis da banca (com respostas prontas)

### "Por que Amazon Bedrock em vez de OpenAI/ChatGPT direto?"

> Três motivos: primeiro, autenticação por IAM elimina o problema de
> chaves de API vazando ou precisando ser rotacionadas — se um
> componente é comprometido, revogo a role, não preciso trocar
> credencial em nada mais. Segundo, latência: chamadas ao Bedrock
> ficam dentro da rede AWS, mesma região dos outros componentes,
> então a rede não pesa. Terceiro, auditoria: cada chamada ao modelo
> aparece automaticamente no CloudTrail, o que para um sistema
> financeiro é uma vantagem gratuita.

### "Por que multiagente em vez de um único LLM grande?"

> Isolamento e especialização. Se eu uso um modelo único com todas as
> tools, ele vê todas ao mesmo tempo — mais escolhas, maior chance de
> ele escolher errado. Com agentes especializados, cada um só tem
> acesso às ferramentas da própria função, e o orquestrador decide
> quando aciona cada um. Isso está documentado como "Propriedade 6"
> do design, com teste automatizado que garante que nenhum agente
> pode acessar tool que não seja da sua política.

### "Como você garante que a IA não dá conselho financeiro ruim?"

> Três camadas. Primeiro, o disclaimer explícito em toda resposta que
> envolve projeção — o texto diz que é apoio à decisão, não
> execução de operação. Segundo, o Agente_Explicador só consolida o
> que os outros agentes acharam com base em dados reais (perfil do
> usuário, carteira dele, indicadores oficiais do BCB) — não sai
> inventando ativos ou taxas. Terceiro, o modelo usado pelo
> orquestrador é forçado por `toolChoice` a consultar a carteira real
> antes de afirmar qualquer valor — corrigi esse comportamento
> justamente por causa de um bug em que a IA respondia com dados
> antigos da memória.

### "Por que reduziu de 6 para 4 agentes? Não perdeu isolamento?"

> Não. Os dois agentes que 'removi' eram para tarefas de leitura
> pura — Perfil e Macroeconômico. Eles só liam dados do DynamoDB e
> retornavam. Envolver um LLM nisso era caro sem ganho — pagava o
> custo de dois modelos por tarefa (o orquestrador chamava o agente,
> que chamava a tool, formatava, e retornava). Transformando em tools
> diretas do orquestrador, ele continua isolado do lado das operações
> que exigem raciocínio (Risco, Seleção de Ativos, Explicador). O
> isolamento que importa está mantido.

### "Como escala? Se tiver 1000 usuários simultâneos?"

> A camada Lambda escala automaticamente até 1000 execuções
> concorrentes por padrão (ajustável). AgentCore Runtime também é
> serverless. DynamoDB usa On-Demand — escala com o tráfego. O gargalo
> real seria custo (Bedrock cobra por token) e limites de conta no
> Bedrock. Para volume real, seria caso de negociar quota com a AWS,
> ou trocar Opus por modelos menores em partes do pipeline. Não é
> problema de arquitetura.

### "Quanto custa?"

> Em desenvolvimento (uso baixo), custa poucos dólares por mês. O
> maior componente de custo em uso real seria o Bedrock: cada
> recomendação completa consome cerca de 10–15 mil tokens no total,
> distribuídos entre Opus e Haiku. Fazendo conta rápida, ~$0,15 por
> recomendação completa hoje. Ou seja, um usuário ativo que faz 10
> perguntas por mês custaria em torno de $1,50 em modelo. O resto da
> infraestrutura é insignificante em comparação.

### "Você faria diferente hoje?"

> Duas coisas. Uma, teria começado com o toolChoice desde o dia zero
> em vez de descobrir depois — perdi tempo tentando corrigir o
> comportamento por prompt. Segunda, teria automatizado o
> versionamento do endpoint do AgentCore Runtime junto com o deploy
> da imagem — perdi tempo debugando o que parecia "deploy que não fez
> efeito" e era só o endpoint apontando para uma versão antiga do
> Runtime.

### "Por que Kiro em vez de Cursor ou VSCode?"

> Porque Kiro trata specs, steering files, agents e hooks como
> primitivas nativas. Nos outros, precisaria configurar cada coisa
> separadamente. Como o projeto durou meses, sem contexto persistente
> entre sessões cada retomada começaria "esquecendo" o que já foi
> decidido. Não é que os outros não funcionem — é que o Kiro sai da
> caixa alinhado com o método de trabalho que documentei.

### "O AI-DLC não é uma metodologia comercial? Você é consultor?"

> Sério: usei o AI-DLC como framework de referência, não segui como
> engajamento comercial completo. Documentei explicitamente o que
> adotei (vocabulário de fases, HITL em pontos críticos, ADRs) e o
> que **não** adotei (assessments multi-persona, workshop com
> sponsor, todas as sete métricas). O método deu estrutura, não deu
> "prova de que fiz consultoria".

---

## Dicas finais

**No dia**:

- Ensaia falando alto **pelo menos 1 vez** antes — a diferença entre
  ler um roteiro na cabeça e falar em voz alta é imensa
- Comida leve, café moderado; nada de coca-cola/energético logo antes
- **Slide de backup**: capturas do sistema baixadas localmente, para
  não depender de internet ou da conta AWS estar respondendo
- Timing folgado: mira 15 min pra ter margem se algo travar

**Se a demo falhar ao vivo**:

- Não perde tempo tentando consertar
- "O ambiente está oscilando, vou seguir pelos screenshots" e segue em
  frente
- Ninguém vai desqualificar por demo travar; **vão** desqualificar se
  você travar tentando fazer funcionar

**Se receber pergunta que não sabe responder**:

- "Não medi isso especificamente, mas o mecanismo seria X — posso
  investigar depois se quiserem." É honestidade, não fraqueza.
- **Nunca invente número**. Se não medi, digo que não medi.

**Postura**:

- Falar com a banca, não com o slide (só olha pra tela pra dar contexto)
- Devagar > rápido. Ansiedade acelera a fala; **respire** entre slides.
- No fim: agradece, mostra que quer perguntas, e senta relaxado.

Boa apresentação!
