# Decisões de Usabilidade e Experiência do Usuário

Este documento registra as decisões tomadas para melhorar a interface e a
experiência de uso do GEVI, com foco no que o usuário percebe: clareza
visual, redução de estados vazios, feedback consistente, e correção de bugs
que afetavam a percepção de confiabilidade do produto.

Complementa `decisoes-agentes-e-latencia.md` (foca em desempenho técnico) e
`defesa-agentcore-bedrock.md` (justifica a escolha das ferramentas AWS).
Serve como referência para o TCC (justificativa de design de UX) e para
onboarding de novos colaboradores.

## Princípio orientador

Ao longo das iterações, aplicamos consistentemente três critérios ao
avaliar cada tela:

1. **Contexto imediato**: o usuário deve conseguir enxergar o essencial (o
   que tem, quanto vale, qual o perfil) sem navegar entre telas.
2. **Feedback proporcional à ação**: cada ação do usuário produz um
   retorno visível — nada de "clique e reze".
3. **Cor de marca como sinal, não como decoração**: o azul-marinho
   (`primary-container: #001a3f`) é usado onde há intenção
   (destaque de dados-chave, ação principal, identidade do produto), não
   uniformemente em todo lugar.

## 1. Reorganização do Dashboard (home)

### Estado inicial

O dashboard herdado do mockup tinha, do topo pra baixo: 4 cards de
indicadores macroeconômicos (Selic/IPCA/Dólar/CDI), um gráfico grande de
"Evolução dos Indicadores" com um seletor de qual indicador exibir, um
painel de "Alertas Inteligentes" e, no fim, um resumo da carteira. Ordem
antiinversa ao que o investidor precisa: os **dados dele** ficavam
embaixo, e os dados de mercado no topo.

### Estado atual

Ordem invertida e conteúdo repensado:

1. **Topo — Resumo da carteira**: três cards em uma linha:
   - Total Investido (R$ com destaque tipográfico)
   - Perfil de Risco (Moderado/Conservador/Arrojado)
   - Última Alteração (data relativa + descrição das mudanças)

2. **Meio — Composição real**: grid de duas colunas:
   - Gráfico de pizza da alocação por categoria (tipo de ativo), com
     total no centro e legenda ao lado (percentuais + valores)
   - Listagem por categoria de investimento com o valor de cada ativo
     individual

3. **Fim — Indicadores macroeconômicos**: 4 cards horizontais para Selic,
   IPCA, Dólar e CDI. São informação de contexto, não de destaque.

Esse reordenamento reflete a hierarquia real de importância para o
usuário: primeiro "o que eu tenho", depois "como está distribuído",
depois "como está o mercado".

## 2. Menu lateral: cor de marca em vez de preto puro

### Problema

A sidebar usava `bg-black` hardcoded — preto puro (`#000000`). O resto da
plataforma usa `#001a3f` (azul-marinho) como cor de identidade, aparecendo
em botões primários, cards de destaque (Total Investido), no centro do
gráfico de pizza, etc. O preto puro na sidebar destoava dessa paleta e
parecia uma escolha por omissão, não por design.

### Correção

Trocado `bg-black` por `bg-primary-container` (token do tema apontando
para `#001a3f`). Nada mais mudou; o azul-marinho já era usado em outros
componentes, então a consistência aumentou sem introduzir uma cor nova.

## 3. Avatar da assistente ClaraInvest

### Problemas encontrados

- A imagem era carregada de uma URL externa (Google user content) que
  poderia expirar a qualquer momento, quebrando o avatar em produção.
- O enquadramento circular estava cortando o rosto: a imagem original é
  vertical (714×1072) com o rosto no terço superior, e `object-cover`
  padrão centraliza (`50% 50%`).

### Correções

- Imagem movida para `front-app/public/clara-avatar.png` e servida pelo
  próprio CloudFront (parte do bucket S3 do frontend). Sem dependência
  externa.
- `object-position: 50% 12%` nos dois pontos onde o avatar aparece
  (cabeçalho do chat e balão de mensagem), focando no rosto e não no
  centro geométrico da imagem.

## 4. "Última alteração" da carteira: com descrição do que mudou

### Trajetória

**V1**: um card no dashboard mostrava só uma lista visual da alocação
por tipo de ativo — redundante com o gráfico de pizza logo abaixo.

**V2**: substituído por "Última Alteração: hoje" (data relativa
calculada em cliente, formato "hoje/ontem/há N dias/data completa" pra
casos antigos). Melhor que a redundância, mas sem *o quê* mudou.

**V3 (atual)**: adicionado um campo `ultimaAlteracaoDescricao` populado
pelo backend a cada `salvar_portfolio`. A função
`_descrever_alteracao_portfolio` compara a carteira anterior (que o
handler já lia antes de sobrescrever) com a nova e gera uma descrição
tipo:

```
Alterou Tijolos (R$ 8.000,00 → R$ 2.000,00)
Alterou Tesouro Selic (R$ 6.000,00 → R$ 5.550,00)
Alterou PETR4 (R$ 4.000,00 → R$ 3.500,00)
E mais 1 alteração(ões)
```

Cada mudança em uma linha (usando `whitespace-pre-line` no CSS pra
preservar as quebras que vêm do backend).

### Decisão de arquitetura importante

Não foi criada uma **tabela de histórico** de alterações. A opção era:

- **Simples (adotada)**: guardar apenas o snapshot mais recente + um item
  de metadados `__meta__` com a descrição da última mudança, ambos na
  tabela `dev-Portfolios` já existente. Não é histórico completo — só
  responde "o que mudou agora".
- **Completa (rejeitada)**: nova tabela `PortfolioHistorico` com um item
  por evento de mudança. Suportaria "ver todas as alterações dos últimos
  30 dias", timeline, etc.

A opção simples foi confirmada explicitamente com o usuário como
prioridade: "faz o simples para nao mudar arquitetura". Trade-off aceito:
o usuário vê só a última mudança, não um histórico navegável.


## 5. Indicadores econômicos: correção do "0,00% vs. mês anterior"

### Sintoma reportado

Os 4 cards de indicadores mostravam sempre `0,00% vs. mês anterior`,
independente do valor real do indicador. Isso quebrava a credibilidade da
tela: um indicador de mercado que "nunca varia" é claramente errado.

### Diagnóstico

Duas Lambdas dividem responsabilidade sobre indicadores (Propriedade 10
do design — separação de leitura/escrita):

- `fn-consulta-APIs`: escreve. Roda a cada 5 minutos (EventBridge),
  consulta o SGS do BCB, extrai `valor_atual` e `valor_anterior` da série
  oficial (penúltima observação — o "mês anterior" real), e grava um item
  novo em `dev-EconomicIndicators`.
- `fn-consulta-indicadores`: lê. Consulta os 2 itens mais recentes e
  calcula a variação.

O bug estava no cálculo da variação em `fn-consulta-indicadores`: usava
`recentes[1].valor` (o segundo item mais recente da tabela) como
"anterior", em vez de `recentes[0].valor_anterior` (o valor da série real
já persistido no item mais recente). Como o EventBridge grava a cada 5
minutos, "recentes[1]" quase sempre tinha o mesmo valor de "recentes[0]"
— a Selic real não muda de minuto em minuto. Variação sempre 0%.

### Correção

Sempre usar `atual.valor_anterior` (que vem da série do BCB, refletindo
o período anterior real — mês anterior para séries mensais, dia anterior
para diárias). Testado em produção: DÓLAR passou a mostrar `+0,49%` real,
Selic e CDI continuam em 0,00% legitimamente (o valor real não mudou
mesmo, o COPOM não se reuniu recentemente).

### Complemento: remoção da linha "X% vs. mês anterior"

Ainda que a variação agora esteja correta, a semântica de "vs. mês
anterior" é ambígua e frágil (o que é o mês anterior de uma cotação de
dólar de sexta-feira? Depende do calendário). Removido por decisão do
usuário: os cards mostram só o valor atual, sem tentar comunicar variação.
Cálculo mantido no backend caso volte a fazer parte da UI depois.

## 6. Gráfico de pizza da alocação

Duas passadas de ajuste visual:

- **Tamanho**: aumentado de `w-48 h-48` (192px) para `w-64 h-64` (256px);
  altura do card de `h-80` para `h-96` para acomodar. O número no centro
  ("Total: R$ X") foi mantido no mesmo tamanho relativo (só o círculo
  cresceu).
- **Legenda**: fonte de `text-xs` para `text-base`; marcador de cor de
  `w-2.5 h-2.5` para `w-3.5 h-3.5`. Legenda era virtualmente ilegível na
  versão anterior; agora dá para bater olho e entender.
- **Consistência com o card ao lado**: "Investimentos por Categoria"
  ganhou a mesma estrutura (título fora do card, card com `h-96` fixo),
  para as duas colunas do grid terem exatamente a mesma altura.

## 7. Notificações: separação semântica entre "marcar como lidas" e "limpar"

### Bug encontrado

O usuário reportou: "mesmo quando limpo, as notificações voltam". O botão
"Limpar" na verdade só chamava `marcarNotificacoesLidas`, que:

- Marcava as notificações **de mercado** como `lida: true` (mantinha na
  lista, só zerava o badge de não-lidas)
- **Não fazia nada** com as **proativas** (concentração alta, exposição
  acima do perfil, baixa diversificação), que são geradas ao vivo por
  `_alertas_proativos` a cada listagem

Resultado: usuário clica, o badge some, fecha o menu, reabre — as
proativas reaparecem porque foram regeneradas ao vivo.

### Correção estrutural

Introduzida a ação `limpar_notificacoes` no proxy, distinta de
`marcar_lidas`:

- **`marcar_lidas`** (comportamento antigo, mantido): só zera o badge
  visual. Notificações continuam na lista.
- **`limpar_notificacoes`** (nova): apaga as de mercado do storage e
  **grava os IDs das proativas atuais** num campo
  `notificacoes_descartadas` do usuário. `_listar_notificacoes` filtra
  esses IDs — a proativa não volta enquanto o ID for o mesmo.

Como o ID de uma proativa é determinístico (ex.: `prov-conc-<assetId>` ou
`prov-risco`), o usuário só volta a vê-la se algo mudar — nova
concentração em ativo diferente, novo perfil, etc. Comportamento
alinhado com a expectativa de "limpar".

### UI: dois botões separados

No dropdown de notificações, dois botões em vez de um:

- **"Marcar como lidas"** (só aparece se houver alguma não-lida) — texto
  em azul-marinho, ação leve.
- **"Limpar"** — texto em vermelho (`text-error`), tratando como ação
  destrutiva (linguagem visual coerente com o resto do sistema, ex.:
  ícone de excluir conversa).

Fica claro para o usuário qual é qual: "quero só marcar como visto" vs
"quero apagar".

## 8. Chat: reorganização visual da tela

Sete pontos de melhoria identificados em revisão de UI, aplicados juntos:

### 8.1 Botão "+ Nova Conversa" em destaque

Antes: borda cinza fina, indistinguível dos itens da lista embaixo.
Agora: fundo azul-marinho (`primary-container`) + texto branco, com
sombra e hover que escurece — a ação principal daquela coluna fica óbvia.

### 8.2 Cards de conversa com peso visual

Antes: linhas de texto soltas, sem hover-state visível, ícone de excluir
sempre à mostra. Agora:

- Ícone `chat_bubble` à esquerda, dando "corpo" ao item
- Título e data empilhados verticalmente
- Hover: fundo cinza-claro (`hover:bg-surface-container`)
- Estado **ativo** (conversa aberta): borda esquerda azul-clara + fundo
  levemente tingido de azul; muito mais claro qual conversa está sendo
  visualizada
- Ícone de excluir com `opacity-0 group-hover:opacity-100` — invisível
  até o mouse passar sobre o item; some da poluição visual e reduz o
  risco de clique acidental

### 8.3 Data em formato relativo

Antes: `dd/mm` (sem ano). Após 3+ meses fica ambíguo — foi 23/09 desse
ano ou do ano passado?

Agora: "hoje", "ontem", "há N dias" para menos de 7 dias; data completa
`dd/mm/yyyy` para o resto. Mesmo padrão que já uso no card "Última
alteração" do dashboard, mantendo consistência entre telas.

### 8.4 Chips de sugestão na conversa vazia

Grande espaço em branco desaparecia quando a conversa está no estado
inicial (só a mensagem de boas-vindas da Clara). Foi aproveitado com
chips clicáveis:

- "Analisar minha carteira"
- "Como está o cenário econômico?"
- "Sugerir um rebalanceamento"
- "Explicar o que é Selic"

Padrão consolidado de UX de assistentes conversacionais (ChatGPT,
Claude, Perplexity) — reduz o "medo da página em branco" e treina o
usuário sobre o que a IA sabe fazer. Estilo visual: fundo azul-pastel
(`secondary-fixed`) com borda azul-clara e texto azul-escuro; grid
2 colunas de tamanho igual (independentemente do comprimento do texto),
com sombra sutil.

### 8.5 Feedback de progresso durante a resposta

Ver seção 7 de `decisoes-agentes-e-latencia.md` para os detalhes
técnicos. No chat, o balão de "Carregando..." estático foi substituído
por um balão com spinner + texto dinâmico que muda ao longo do
processamento: "Consultando seu perfil...", "Analisando sua carteira...",
"Preparando a explicação...", etc.

### 8.6 Disclaimer da IA com contraste melhor

Antes: `text-[10px]` + `opacity-70` — quase invisível. Aviso legal
importante ficou legível: `text-[11px]` sem opacidade extra. Mensagem
mantida ("A IA da GEVI pode gerar informações imprecisas. Verifique
métricas importantes.").


## 9. Minha Conta: cartão de resumo + perfil do investidor

### Estado inicial

A tela tinha só dois formulários lado a lado: "Alterar Senha" e "Dados
Cadastrais". Nenhum resumo, nenhum contexto sobre o perfil de
investimento — o usuário nunca via o resultado do questionário de
suitability em lugar algum depois do onboarding.

### Análise a partir de um protótipo do usuário

O usuário compartilhou um protótipo antigo com "Cartão de resumo" +
"Perfil do Investidor" + "Análise da ClaraInvest" + "Preferências de
alertas". Analisado ponto a ponto (documentado nesta conversa):

- **Cartão de resumo**: implementado (nome, e-mail, perfil de risco,
  valor da carteira)
- **Perfil do Investidor** com tolerância a risco e nível de
  conhecimento: implementado, com botão "Refazer questionário de perfil"
  que leva a `/onboarding`
- **"Objetivo Principal"** e **"Horizonte de investimento"** no card de
  perfil: rejeitados, porque esses dados são calculados no questionário
  (`avaliar_perfil`) mas *não persistidos* — mostrar seria inventar dado
- **Análise da ClaraInvest** (texto gerado pela IA sobre o perfil):
  rejeitada por enquanto — exigiria chamada ao LLM só para essa tela
  (custo/latência), sendo que o Chat já oferece isso sob demanda
- **Preferências de alertas** na mesma tela: rejeitada; já existe em
  Configurações, juntar sobrecarrega

### Resultado

Duas seções novas adicionadas sem invadir o que já existia:

- Cartão superior largo em fundo azul-marinho: avatar em círculo, nome
  + email do usuário, e dois "campos-métrica" (Perfil do Investidor,
  Valor da Carteira) usando o mesmo padrão tipográfico do dashboard
- Card "Perfil do Investidor" na coluna esquerda (embaixo de "Alterar
  Senha"): Tolerância a Risco + Nível de Conhecimento + botão neutro
  "Refazer questionário de perfil" com fundo cinza-claro (não é ação
  principal da tela)

## 10. Bugs corrigidos que afetavam a percepção de confiabilidade

Vários bugs foram encontrados e corrigidos ao longo das iterações. Os
que impactam diretamente a experiência do usuário estão listados aqui;
detalhes técnicos completos ficam nos comentários do código e nos
outros docs.

### 10.1 "A carteira não salva" (frontend)

**Causa raiz**: o botão "Salvar carteira" bloqueava silenciosamente com
uma mensagem genérica embaixo, difícil de notar, quando havia uma linha
totalmente vazia (usuário clicou "Adicionar ativo" e ainda não
preencheu) ou parcialmente preenchida (nome sem valor). A carteira
inteira parecia "não salvar".

**Correção** em `investimentos/page.tsx`:
- Linhas totalmente vazias são **ignoradas** no salvamento (não bloqueiam)
- Linhas incompletas continuam bloqueando, mas a mensagem de erro agora
  aponta *qual* ativo está com problema ("Informe um valor maior que
  zero para 'Tesouro Selic'")

### 10.2 "O histórico de chat volta depois de apagado" (backend)

**Causa raiz**: `_append_mensagem` no proxy usava `update_item` do
DynamoDB sem `ConditionExpression`. Comportamento padrão do `update_item`:
se a chave não existe, o item é **criado**. O worker assíncrono do chat
(que processa a resposta da IA em background e leva ~1-2 minutos) fazia
`_append_mensagem` no fim para gravar a resposta — se o usuário tivesse
excluído a conversa nesse meio tempo, o `update_item` recriava o item
do zero, "ressuscitando" a conversa apagada.

**Correção**: adicionado `ConditionExpression="attribute_exists(userId)"`.
Se a conversa foi excluída, a gravação falha silenciosamente
(`ConditionalCheckFailedException` capturado e ignorado), a resposta da
IA é descartada, e a conversa continua excluída — o esperado. Testado
com script Python que reproduziu exatamente o cenário do bug (criar
conversa, excluir imediatamente, esperar a resposta da IA chegar,
confirmar que a conversa continua excluída via `obter_conversa`).

### 10.3 "IA responde com dados obsoletos sobre a carteira"

**Causa raiz**: memória de longo prazo do AgentCore guardava registros
antigos de sessões anteriores (R$ 13.000, ativos que não existem mais)
e o modelo respondia com esses valores sem consultar a tool
`analisar_portfolio` da carteira real (R$ 18.000, composição diferente).

**Correção**: descrita em detalhe em `decisoes-agentes-e-latencia.md`
seção 8 — heurística + `toolChoice` forçado da Converse API.

### 10.4 "IA reformula o nome do ativo" (mesmo tipo de causa)

**Sintoma**: usuário cadastrou um ativo chamado "Tijolos" (FII), mas a
IA respondia com "FIIs Tijolo" (o nome que estava na memória de longo
prazo, de conversas anteriores).

**Correção**: reforço no prompt dos agentes Selecao_Ativos e Explicador:
usar o `assetId` **exatamente como veio da tool**, nunca reformular,
mesmo que pareça abreviado ou diferente da memória. Sem `toolChoice` para
esse caso porque o dado *estava* vindo da tool (o valor total estava
certo, R$ 18.000); só o rótulo foi trocado — problema de prompt, não de
falta de consulta.

### 10.5 Validação de idade aceitava datas inválidas

**Sintoma**: o formulário de perfil aceitava data de nascimento no
futuro ou de menor de 18 anos.

**Correção**: função `validarNascimento` em `minha-conta/page.tsx` e
`onboarding/page.tsx`, além de `min`/`max` no `<input type="date">`.
Regras: 18 ≤ idade ≤ 120, sem datas futuras.

### 10.6 Conversas "voltavam" por falha de deploy (não bug de código)

**Sintoma**: correções aplicadas em `agentcore/agentes.py` não pareciam
fazer efeito em produção mesmo depois do deploy.

**Causa raiz** (não é bug do código, é gotcha operacional): o Runtime
AgentCore tem endpoints nomeados com `liveVersion` própria. Atualizar a
versão do Runtime (`update-agent-runtime`) não atualiza o endpoint —
tem que rodar `update-agent-runtime-endpoint` também. Documentado
detalhadamente na seção 9 de `decisoes-agentes-e-latencia.md` e como
memória de repositório para futuras sessões.

## Resumo

| Área | Antes | Depois |
|---|---|---|
| Dashboard | Indicadores de mercado no topo, carteira no fim | Carteira no topo, mercado no fim |
| Sidebar | Preto puro | Azul-marinho de identidade |
| ClaraInvest avatar | URL externa (pode expirar) | Local, enquadrado no rosto |
| Última alteração | Card redundante com o gráfico | Data + diff das mudanças em linhas |
| Variação de indicadores | Sempre 0% (bug) | Correta, ou omitida quando ambígua |
| Gráfico de pizza | Pequeno, legenda ilegível | Maior, legenda em `text-base` |
| Notificações | "Limpar" só marcava como lida | "Marcar como lidas" + "Limpar" |
| Chat vazio | Espaço morto | Chips de sugestão em azul |
| Chat em execução | Spinner sem contexto | Etapa atual do pipeline |
| Chat: lista de conversas | Linhas soltas, delete sempre visível | Cards com ícone, delete on-hover |
| Chat: data de conversa | `dd/mm` sem ano | "hoje / ontem / há N dias" |
| Minha Conta | Só formulários | + Cartão resumo + Card perfil |
| Idade no cadastro | Aceitava inválida | Valida 18–120, sem futuro |

Cada linha corresponde a um sinal de qualidade para o TCC:
"identificamos, quantificamos e resolvemos".
