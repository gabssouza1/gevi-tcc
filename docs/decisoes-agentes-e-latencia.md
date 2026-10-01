# Decisões de Arquitetura: Agentes, Latência e Uso de LLM

Este documento registra o processo de refinamento da arquitetura de agentes
do GEVI: por que ela mudou de um agente único, para 6 agentes, e por fim para
4 agentes com modelo dual — e o raciocínio por trás de cada decisão. Serve
como referência para o TCC (justificativa de design) e para quem for dar
manutenção no `agentcore/agentes.py`.

## Linha do tempo das arquiteturas

| # | Arquitetura | Motivo da mudança |
|---|---|---|
| 1 | 1 agente único (LLM com tool-use, acesso direto a todas as tools) | Implementação inicial mais simples |
| 2 | 6 agentes reais (Orquestrador + 5 especialistas, cada um seu próprio loop de LLM) | Pedido explícito: "quero 5 agentes + 1 orquestrador" — isolamento real de tools por agente (Propriedade 6 do design), fiel ao design.md original |
| 3 | 4 agentes reais (Orquestrador + Risco + Selecao_Ativos + Explicador) | Perfil e Macroeconômico não exigem raciocínio — viraram tools diretas do Orquestrador |
| 4 (atual) | 4 agentes + modelo dual (Opus/Haiku) | Reduzir custo/latência sem perder qualidade onde ela importa |

A arquitetura 2 introduziu um problema real: uma recomendação completa levava
**~123–137 segundos**. As seções abaixo descrevem como isso foi diagnosticado
e reduzido para **~66–80 segundos**, e os critérios usados para decidir "isso
precisa de um agente" vs. "isso é uma leitura direta".

## Diagnóstico: onde o tempo estava indo

Antes de otimizar, instrumentei `agentcore/servidor.py` para logar a duração
de cada chamada ao modelo (`_chamar_modelo`) e ao Knowledge Base
(`_recuperar_kb`), com o modelo usado e o `stopReason`. Isso expôs a timeline
real de uma invocação via CloudWatch Logs
(`/aws/bedrock-agentcore/runtimes/<runtime>-<endpoint>`), em vez de otimizar
com base em suposição.

Os dois achados que mais pesavam:

1. **Chamadas de tool/agente em série quando podiam ser paralelas.** O
   Orquestrador chamava um agente, esperava a resposta, e só então chamava o
   próximo — mesmo quando os agentes eram independentes entre si (não
   dependiam da resposta um do outro).
2. **O Agente_Explicador consultava o Knowledge Base uma vez por conceito,
   sequencialmente** (3–4 consultas de ~0,5s cada, mas cada uma seguida de
   uma chamada inteira ao LLM para decidir a próxima) — isso por si só
   consumia ~90 segundos em alguns casos.

## Decisões tomadas

### 1. Paralelizar tool-calls do mesmo turno

A Bedrock Converse API já permite ao modelo pedir várias ferramentas no mesmo
turno (`stopReason: tool_use` com múltiplos blocos `toolUse`). O código
anterior executava essas chamadas uma a uma, num loop simples. Passei a
executá-las em paralelo com `ThreadPoolExecutor`
(`_executar_usos_em_paralelo`), preservando:

- **Ordem determinística de bookkeeping**: como threads terminam em ordem
  variável, o registro de "quais agentes/tools foram usados" não pode
  depender de efeito colateral dentro da thread — usa-se o callback
  `ao_resultado`, chamado na thread principal, na ordem de *submissão* (não
  de conclusão).
- **Isolamento de erro por chamada**: uma falha numa tool não cancela as
  demais; volta ao modelo como `toolResult` de status `error`.

Ajustei os prompts do Orquestrador e do Explicador para *pedir* paralelismo
explicitamente (ex.: "chame os 4 agentes/tools independentes no MESMO turno,
não espere um responder para chamar o próximo"). Sem essa instrução, o
modelo tende a serializar por padrão.

### 2. Perfil e Macroeconômico não são agentes — são tools diretas

Esta foi a mudança de maior impacto estrutural. Um "agente" no sentido usado
aqui é um loop de tool-use com seu próprio LLM: ele decide *como* interpretar
uma tarefa antes de agir. Isso tem custo (mínimo 2 chamadas ao LLM: uma para
decidir chamar a tool, outra para formatar a resposta) e só se justifica
quando há de fato uma decisão a tomar.

Perfil (`gerenciar_perfil_investidor`) e Macroeconômico
(`consultar_indicadores_economicos`) são consultas de leitura sem ambiguidade
— não há "interpretação" a fazer, só relatar o dado que já está no DynamoDB.
Delegar isso a um sub-agente dedicado era pagar o custo de um LLM sem ganho
de qualidade nenhum.

**Critério adotado para decidir se algo precisa de um agente (LLM) ou não:**

| Precisa de agente (LLM) | Não precisa — tool/leitura direta |
|---|---|
| Envolve julgamento ou síntese de múltiplas fontes (ex.: Explicador consolidando 4 achados) | É uma consulta 1:1 a um dado já persistido |
| A resposta muda de forma (linguagem, estrutura) dependendo do contexto do usuário | A resposta é sempre o mesmo dado, só formatado |
| Decide *quais* ferramentas usar dentre várias possíveis | Só existe uma forma de resolver a tarefa |
| Erros de interpretação têm custo alto (ex.: classificar risco incorretamente) | O dado retornado já é a "verdade" (vem do Dynamo) |

Aplicando esse critério: Perfil e Macro caem claramente no lado direito.
Risco e Seleção de Ativos ficam no meio — não fazem julgamento complexo, mas
precisam decidir parâmetros e formatar uma síntese a partir de múltiplos
retornos de tool, então permaneceram como agentes (ver decisão 4 abaixo sobre
o modelo usado por eles). O Explicador fica claramente no lado esquerdo —
consolida 4 fontes e adapta linguagem ao perfil do usuário.

O Orquestrador agora tem, na mesma lista de ferramentas, duas tools diretas
do Gateway e três meta-tools que acionam agentes especialistas. Do ponto de
vista do modelo, ambas são só "ferramentas que ele pode chamar" — a diferença
(agente vs. tool direta) é interna à implementação (`_TOOLS_DIRETAS_ORQUESTRADOR`
em `agentcore/agentes.py`), não aparece no prompt como uma distinção
especial.

### 3. Atalho de encerramento pós-Explicador

O Orquestrador, ao receber a resposta do Agente_Explicador, fazia mais uma
chamada ao LLM só para "repassar" esse texto como resposta final — uma
chamada inteira ao Opus (10–15s) que não agregava nada, porque o texto do
Explicador já é a resposta definitiva.

Implementado como `verificar_atalho` em `_loop_tool_use`: quando a resposta
do Explicador está disponível, o loop encerra imediatamente com esse texto,
sem gastar outro round-trip.

### 4. Modelo dual: Claude Opus vs. Claude Haiku

Todos os agentes usavam o mesmo modelo (Claude Opus) independente da
complexidade da tarefa. Opus é o modelo mais lento e caro da família — se
justifica onde a qualidade de raciocínio ou de redação em linguagem natural
importa de fato:

- **Agente_Orquestrador**: decide quais ferramentas acionar, para pedidos
  potencialmente ambíguos.
- **Agente_Explicador**: escreve o único texto que o usuário lê diretamente,
  adaptado ao nível de conhecimento dele.

**Agente_Risco** e **Agente_Selecao_Ativos** só produzem notas internas
concisas (números e fatos, sem formatação nem "voz" — ver a instrução
`_NOTA_CONCISAO` nos prompts) que são consumidas pelo Orquestrador/Explicador,
não pelo usuário final. Não há perda de qualidade percebida em trocar o
modelo desses dois por Claude Haiku, bem mais rápido.

Cada especialista carrega o próprio modelo na sua spec
(`AGENTES_ESPECIALISTAS[nome]["modelo"]`), propagado por `_loop_tool_use` até
`_chamar_modelo`. A permissão IAM (`bedrock:InvokeModel`) foi ajustada para
autorizar os dois inference profiles (Opus e Haiku), cross-region.

### 5. Consultas ao Knowledge Base em lote

O Explicador consultava o KB uma vez por conceito, em turnos separados. O
prompt foi ajustado para pedir todas as consultas relevantes no mesmo turno
(paralelas, pela mudança 1), reduzindo de ~90s para ~1,5s essa etapa.

### 6. Correção incidental: `max_tokens`

Ao medir o impacto das mudanças acima, uma resposta do Explicador terminou
com `stopReason: max_tokens` (cortada no meio) — o limite de 2048 tokens era
insuficiente para respostas longas com tabelas/markdown. Ajustado para 4096.
Não é uma decisão de latência, mas foi encontrado durante o mesmo ciclo de
instrumentação/medição.

## Resultado medido

Medido via CloudWatch Logs do Runtime, para o mesmo cenário (recomendação de
investimento completa, mesmo usuário e carteira de teste):

| Etapa | Tempo (log do Runtime) |
|---|---|
| Antes de qualquer otimização (6 agentes, tudo sequencial, tudo em Opus) | ~123,5 s |
| + paralelismo de tool-calls (sem resolver o KB sequencial) | ~132 s (sem ganho líquido) |
| + KB em lote + prompts concisos + atalho pós-Explicador | ~87,9 s |
| + Perfil/Macro como tools diretas (4 ações no mesmo turno) | ~70,3 s |
| + modelo dual (Haiku para Risco/Selecao_Ativos) | **~66,8–80,4 s** |

A redução total é de ~40–45% em relação à primeira versão com 6 agentes. A
segunda linha da tabela é intencional: mostra que paralelizar tool-calls, por
si só, não resolveu nada até o gargalo real (KB sequencial) ser identificado
e corrigido — otimização sem medir a timeline real levaria a esforço mal
direcionado.

## Trade-offs aceitos

- **Menos "puro" como demonstração de multiagente**: com Perfil/Macro como
  tools diretas, a contagem de "agentes reais" caiu de 6 para 4. Isso foi
  uma escolha deliberada (confirmada com o usuário) de priorizar
  desempenho sobre a contagem de agentes, já que os dois "agentes" removidos
  não agregavam raciocínio algum.
- **Complexidade de bookkeeping concorrente**: paralelizar tool-calls exigiu
  cuidado para manter listas como `agentes_usados`/`ferramentas_usadas`
  determinísticas apesar da execução em threads (ver `ao_resultado` em
  `_loop_tool_use`). Testado com fakes que simulam múltiplas tool-uses no
  mesmo turno (`tests/test_prop06_e_pipeline.py`).
- **Dependência de dois modelos em vez de um**: a troca por modelo dual
  aumenta a superfície de configuração (dois `modelId`, duas entradas na
  policy IAM) em troca de latência menor. Se o Haiku for descontinuado ou
  tiver comportamento divergente do Opus para os prompts atuais, Risco e
  Selecao_Ativos são os pontos a revisar primeiro.
- **Latência ainda não é "instantânea"**: ~70–80s para uma recomendação
  completa é uma redução significativa, mas ainda é perceptível para o
  usuário. O passo mais caro remanescente é a redação final do Explicador em
  Opus (~30s) — reduzir isso further exigiria trocar esse modelo também, o
  que impactaria a qualidade do texto que o usuário lê (trade-off não
  aplicado nesta rodada).

## Onde isso vive no código

- `agentcore/agentes.py`: lógica dos agentes, prompts, modelo por agente
  (`AGENTES_ESPECIALISTAS`), tools diretas (`_TOOLS_DIRETAS_ORQUESTRADOR`),
  paralelismo (`_executar_usos_em_paralelo`), atalho (`verificar_atalho`).
- `agentcore/servidor.py`: chamada real ao Bedrock (`_chamar_modelo`) e ao KB
  (`_recuperar_kb`), com logging de duração.
- `infra/stacks/agents_stack.py`: permissões IAM dos dois modelos
  (`InvocarModelosLLM`).
- `.kiro/specs/multiagent-investment-advisor/design.md`: diagrama e
  descrição funcional atualizados para a arquitetura de 4 agentes.
- `tests/test_prop06_e_pipeline.py`: testes de isolamento de tools por agente
  e de integração (paralelismo, atalho, modelo por agente).

## 7. Feedback de progresso incremental (redução da latência percebida)

Reduzir a latência real de ~123s para ~70s foi metade do problema. A outra
metade: **o usuário continua olhando para um spinner sem saber se o sistema
travou ou está processando**. Latência percebida é uma dimensão separada da
latência bruta — 70s sem feedback são psicologicamente mais longos que 70s
com um sinal claro de progresso.

### Problema estrutural

A chamada ao AgentCore Runtime é uma requisição HTTP síncrona: o proxy
Lambda invoca `/invocations` e espera o resultado inteiro. Não há canal de
volta do Runtime para o proxy durante a execução — só o payload final.
Portanto, streaming tradicional (chunks HTTP conforme o processamento anda)
não é viável nesta camada sem trocar o API Gateway REST por uma alternativa
com suporte a streaming (Function URL, HTTP API), o que exigiria alteração
de infraestrutura.

### Solução escolhida (nível 1)

Duas opções foram analisadas:

- **Nível 1 — progresso por etapa**: o Runtime grava a etapa atual do
  pipeline num campo do job (DynamoDB), e o polling do frontend (que já
  existia para saber quando o resultado ficou pronto) exibe esse texto
  enquanto muda. Não é streaming de tokens; é feedback de "em qual etapa
  estou".
- **Nível 2 — streaming real de tokens**: a resposta final apareceria
  palavra por palavra na tela, como ChatGPT/Claude fazem. Exigiria mudar o
  API Gateway REST por Function URL com Lambda response streaming, mais
  invasivo, e a Lambda proxy assumir uma conexão HTTP longa por usuário
  (compatibilidade com o modelo assíncrono de job perderia a razão de
  existir).

Foi implementado o **nível 1** primeiro pela relação custo/benefício: nenhuma
mudança de infraestrutura, cabe na arquitetura atual, mantém o proxy
stateless e cobre a queixa central (o "vazio de 2 minutos"). O nível 2 fica
como próxima iteração se a resposta ainda parecer lenta com esse feedback.

### Implementação

- `agentes.py`: `executar_orquestrador` aceita um callback `ao_progresso(etapa)`
  chamado *antes* da execução paralela de cada turno de tool-uses (via
  `ao_iniciar_usos` em `_loop_tool_use`). Mapa `_ETAPA_POR_TOOL` traduz o
  nome técnico da tool em uma descrição em português ("Analisando sua
  carteira e selecionando ativos", etc.). Quando o modelo pede múltiplas
  tools no mesmo turno (paralelismo), as descrições são concatenadas com
  " + ".
- `servidor.py`: `_criar_reportador_progresso(job_id)` retorna um callback
  que faz `update_item` na tabela `dev-ChatJobs` com o campo `etapa`. Uso
  `ConditionExpression="attribute_exists(jobId)"` para não recriar um job
  apagado (mesma classe de bug corrigida em `_append_mensagem`).
- `handler.py` (proxy): o worker inclui `jobId` no payload enviado ao
  Runtime; `_status_job` inclui o campo `etapa` na resposta de status.
- `lib/api.ts` (frontend): `enviarChat` aceita `onProgresso(etapa)`
  disparado quando o campo `etapa` do polling muda.
- `chat/page.tsx`: substitui o texto fixo "Analisando..." pelo texto
  dinâmico da etapa atual, com spinner.

### Aprendizado: IAM implícita

Dar ao Runtime permissão de escrita na tabela de jobs exige uma política
IAM adicional (`dynamodb:UpdateItem` escopado à tabela `dev-ChatJobs`).
Optei por uma **policy inline separada** (`ProgressoJobChat`) em vez de
alterar a policy gerenciada pelo CDK, porque a tabela `dev-ChatJobs` foi
criada fora do IaC — o CDK sobrescreveria a policy num próximo `cdk
deploy`. A separação evita drift automático.

### Resultado medido

Confirmado em produção com uma pergunta de recomendação completa:

- Segundo 2: "Consultando seu perfil de investidor + Consultando indicadores
  econômicos + Analisando o risco da carteira + Analisando sua carteira e
  selecionando ativos" (4 tools independentes chamadas em paralelo)
- Segundo 14: "Analisando o risco da carteira" (turno seguinte)
- Segundo 28: "Preparando a explicação da resposta" (Agente_Explicador)
- Segundo 52: `done` — resposta na tela

O usuário vê 4 estados distintos e informativos ao longo da resposta, em
vez de silêncio.


## 8. `toolChoice` forçado: instrução de prompt não é garantia

Descoberta relacionada à latência apenas indiretamente, mas fundamental
para a defesa técnica: **instrução textual num prompt não garante execução
determinística**. Um bug real ilustrou isso.

### Sintoma

Ao perguntar "qual o valor total da minha carteira?", o modelo respondia
com valores obsoletos (R$ 13.000, ativos que não existem mais na carteira
real), usando a memória de longo prazo do AgentCore Memory em vez de
consultar `analisar_portfolio` — mesmo com a regra "REGRA OBRIGATÓRIA:
chame Agente_Selecao_Ativos antes de citar valores" explícita no prompt
do Orquestrador, marcada como "sem exceção", com exemplos e reforço.

### Duas tentativas de correção via prompt (ambas falharam)

1. **Regra adicionada no bloco de instruções**: "cuidado com a memória de
   longo prazo... nunca cite valores/ativos que não vieram da tool". Modelo
   ignorou; respondeu direto da memória sem chamar tool alguma
   (`agentes_usados: []`).
2. **Aviso isolado e reforçado no fim do system prompt** (posição de maior
   peso de atenção), gerado dinamicamente só para turnos que a heurística
   de palavras-chave detectou como pedido de carteira. Também ignorado.

### Correção definitiva: forçar por API

A Bedrock Converse API tem `toolConfig.toolChoice`, que aceita três
formatos:

- `{"auto": {}}` (padrão): o modelo decide se e qual tool chamar
- `{"any": {}}`: o modelo é obrigado a chamar alguma tool, mas escolhe qual
- `{"tool": {"name": "..."}}`: o modelo é obrigado a chamar a tool
  específica

A implementação: `_mensagem_pede_dados_de_carteira()` detecta por
palavra-chave (carteira, portfólio, patrimônio, quanto tenho investido...);
quando dispara, `executar_orquestrador` passa
`{"tool": {"name": "consultar_agente_selecao_ativos"}}` como
`tool_choice_primeiro_turno` para o `_loop_tool_use`. A partir do segundo
turno (com o resultado da tool em mãos), volta a `auto` — o modelo já
viu o dado e precisa decidir o que fazer com ele.

### Aprendizado extraído (peça de defesa)

**Quando comportamento é crítico, forçar pela API, não pelo prompt.**
Instruções de prompt são "sugestões elaboradas" — o modelo pode ignorar
por vários motivos (contexto competindo pela atenção, memória parecendo
autoritativa, custo de resposta parecendo alto). `toolChoice` é uma
restrição estrutural no espaço de decisão do modelo: ele *não pode*
responder sem chamar a tool.

### Cobertura de teste

Dois testes específicos garantem que a heurística e o `toolChoice` continuam
funcionando após mudanças futuras no prompt
(`tests/test_prop06_e_pipeline.py`):

- `test_orquestrador_forca_tool_choice_quando_pergunta_e_sobre_carteira`:
  registra o `tool_choice` recebido pelo modelo em cada turno; afirma que o
  primeiro turno recebe `{"tool": {"name": "consultar_agente_selecao_ativos"}}`
  e os seguintes recebem `None` (auto).
- `test_mensagem_pede_dados_de_carteira`: parametrizado com 8 mensagens
  típicas (positivas e negativas) para travar a heurística de detecção.

## 9. Gotcha operacional: endpoints do AgentCore Runtime

Aprendizado importante durante o mesmo ciclo de correção, registrado
também como memória de repositório para não cair na mesma armadilha:

`aws bedrock-agentcore-control update-agent-runtime` cria uma nova versão
do Runtime, mas **NÃO atualiza automaticamente os endpoints nomeados** que
apontam para ele. Cada endpoint tem uma `liveVersion` própria e continua
servindo a versão antiga até ser explicitamente apontado para a nova
(`update-agent-runtime-endpoint`).

Isso causou 4 ciclos de "deploy sem efeito" durante a correção do bug de
memória, porque `get-agent-runtime` mostrava a versão nova como READY
enquanto a Lambda proxy (que invoca via `qualifier=dev_prod`) continuava
chamando o código antigo. Só quando percebi que o endpoint `dev_prod`
estava congelado na versão 19 (de 5 dias atrás) o problema virou visível.

**Fluxo de deploy correto** após mudar código do agentcore:

1. `python3 scripts/configurar_agentcore.py` — build + push da imagem
2. `update-agent-runtime` — cria nova versão do Runtime (não incremental:
   tem que reenviar role, network, env vars, authorizer, headers de
   novo, senão os campos ausentes viram default)
3. Ler `RUNTIME_QUALIFIER` da Lambda para saber qual endpoint é o real
4. `update-agent-runtime-endpoint --endpoint-name <qualifier> --agent-runtime-version <N>`
5. Poll até `status=READY` e `liveVersion=N`
6. **Só então** testar

Esse detalhe é relevante para a defesa como característica ainda em
evolução do produto (o AgentCore ainda é relativamente novo em
disponibilidade geral): dá controle fino de deployments blue/green por
endpoint, mas exige que a operação saiba dessa mecânica.

