## 3.11 Metodologia de Desenvolvimento Assistido por IA

### 3.11.1 Visão Geral

Além das tecnologias de produto descritas em 3.9 e da organização do
sistema descrita em 3.10, a construção do GEVI adotou uma metodologia
explícita de **desenvolvimento assistido por IA**, isto é, um conjunto de
práticas, ferramentas e artefatos versionáveis que estruturaram o diálogo
entre o pesquisador e os modelos de linguagem utilizados ao longo da
implementação.

Essa decisão metodológica se justifica pela natureza do próprio objeto do
trabalho. Sistemas multiagentes concentram parte significativa da sua
complexidade em artefatos que não são código tradicional — *prompts* em
linguagem natural, orientações de contexto, descrições de ferramentas e
fluxos de decisão —, e tratar esses artefatos de forma informal
inviabilizaria tanto a reprodutibilidade quanto a rastreabilidade de
decisões, requisitos exigíveis em um trabalho acadêmico.

Para operacionalizar essa metodologia, foram adotados dois elementos:
o **Kiro IDE** como ambiente de desenvolvimento (3.11.2) e o framework
**AI-DLC** (*AI Development Lifecycle*), da AWS Professional Services, como
referência conceitual para organização das fases de trabalho (3.11.3).

### 3.11.2 Kiro IDE como Ambiente de Desenvolvimento

O Kiro é uma IDE *agentic* desenvolvida pela Amazon, construída sobre o
Visual Studio Code, cujo diferencial frente a editores com assistentes de
IA tradicionais é tratar o agente como componente de primeira classe, com
primitivas versionáveis que persistem o contexto do projeto entre sessões
de trabalho. Foram quatro as primitivas utilizadas no desenvolvimento do
GEVI:

- **Specs** — a especificação estruturada do sistema (requisitos, design
  e plano de tarefas) foi mantida no diretório
  `.kiro/specs/multiagent-investment-advisor/`, servindo como fonte única
  de verdade sobre o escopo e sendo recarregada automaticamente a cada
  nova sessão de trabalho. Essa prática substituiu a tradicional
  dispersão de notas em arquivos avulsos ou conversas efêmeras com
  modelos de linguagem.
- **Steering files** — arquivos em `.kiro/steering/` descrevem as
  convenções do projeto (padrões de segurança, estilo de código,
  *workflow* de *commit*, orientações para redação técnica) e são
  injetados automaticamente no contexto de cada interação com o agente.
  Funcionam como uma "constituição" do projeto, evitando que convenções
  precisem ser recontextualizadas a cada sessão.
- **Hooks** — automações declaradas em `.kiro/hooks/` reagem a eventos
  da IDE (salvamento de arquivo, submissão de *prompt*, execução de
  ferramentas) para disparar verificações determinísticas, como
  execução de *lint*, testes de regressão e validações antes de
  *commits*.
- **Model Context Protocol (MCP)** — a IDE suporta nativamente
  servidores MCP, utilizados no projeto para integrar documentação
  oficial da AWS e consultas técnicas diretamente ao fluxo de
  desenvolvimento, sem alternância manual entre janelas de navegador.

Em termos de autonomia, foram utilizados dois modos distintos conforme o
risco da tarefa:

- **Autopilot**, para operações mecânicas e de baixo risco (renomeações,
  aplicação de padrões de código, geração de testes a partir de
  especificação já aprovada), com revisão em bloco das alterações.
- **Supervised**, para operações de alto impacto (mudanças em
  infraestrutura AWS, edições em *prompts* de produção, implantações),
  com aprovação explícita a cada alteração.

A escolha pelo Kiro em detrimento de alternativas (Visual Studio Code com
Copilot, Cursor, Claude Code CLI) deveu-se à presença integrada dessas
primitivas no mesmo ambiente. Em alternativas, parte do papel do
*steering* pode ser cumprida por convenções próprias de cada ferramenta
(como `.cursorrules`), mas não há equivalente nativo para o conjunto
*spec* + *steering* + *hooks* + MCP operando de forma coordenada.

### 3.11.3 AI-DLC como Framework de Referência

O *AI Development Lifecycle* (AI-DLC) é o framework metodológico da AWS
Professional Services para engajamentos de desenvolvimento assistido por
IA. Organiza o trabalho em quatro marcos sequenciais — M1 (*Align &
Configure*), M2 (*Prove Value*), M3 (*Adapt & Integrate*) e M4
(*Scale & Handover*) —, acompanhados por sete métricas canônicas, um
*AI Operating Model* que define quais decisões podem ser tomadas
autonomamente pelo agente de IA e quais exigem revisão humana, e
atividades formais de gestão de mudança organizacional.

É necessário reconhecer, por honestidade acadêmica, que o AI-DLC foi
adotado neste trabalho como **referência conceitual**, não como
framework seguido estritamente. O método, em seu formato canônico, foi
projetado para engajamentos de doze ou mais semanas com equipes de seis
ou mais pessoas, com patrocinador executivo, *workshops* multi-persona e
avaliações formais de maturidade — contexto incompatível com um Trabalho
de Conclusão de Curso individual. Assim, foram adotados os seguintes
elementos do framework:

- **Vocabulário das fases M1 a M4** para organização mental do trabalho,
  utilizado para distinguir atividades de definição (M1), implementação
  e prova de valor (M2), integração e polimento (M3) e empacotamento
  final (M4).
- **Princípio de *Human-In-The-Loop* (HITL)** em pontos críticos:
  toda alteração em *prompts* que afetam o comportamento em produção,
  toda mudança de infraestrutura via IaC e toda implantação exigiram
  revisão e aprovação manual antes da execução.
- **Rastreabilidade requisito ↔ código**, operacionalizada por
  comentários explícitos no código referenciando requisitos
  (ex.: `# Req 7.3`) e por uma suíte de testes de propriedade
  (`tests/test_prop*.py`) que valida invariantes numeradas da
  especificação.
- **AI Operating Model implícito**, não formalizado em documento
  próprio, mas praticado por meio dos *steering files* e da disciplina
  de uso dos modos Autopilot e Supervised descritos em 3.11.2.

Não foram adotados do AI-DLC, por incompatibilidade de escala, a
avaliação formal multi-persona de maturidade, o *workshop* com
patrocinador executivo, as atividades de *Organizational Change
Management* (comunicação organizacional, capacitação de *champions*,
planejamento de *rollout*) e a medição formal das sete métricas
canônicas — em particular das métricas que exigiriam uma linha de base
*pré-projeto* comparável, ausente em um trabalho individual.

### 3.11.4 Rastreabilidade e Evidências do Método

A aplicação da metodologia descrita acima deixou traços verificáveis no
próprio repositório do projeto, servindo como evidência da sua adoção
efetiva e não meramente declarativa:

- **Especificação versionada** em
  `.kiro/specs/multiagent-investment-advisor/`, contendo os documentos
  de requisitos, *design* e plano de tarefas que orientaram a
  implementação.
- **Convenções versionadas** em `.kiro/steering/`, cobrindo padrões AWS,
  segurança, *workflow* de contribuição e orientações de redação
  técnica aplicadas ao longo do desenvolvimento.
- **Automações versionadas** em `.kiro/hooks/`, com os eventos e as
  ações disparadas pela IDE durante o ciclo de desenvolvimento.
- **Rastreabilidade requisito ↔ teste** nos arquivos
  `sistema/tests/test_prop*.py`, que referenciam explicitamente
  propriedades numeradas da especificação (ex.: *Propriedade 6* —
  isolamento de *tools* por agente; *Propriedade 10* — operações
  somente-leitura em Lambdas de indicadores).
- **Registros de decisão arquitetural** distribuídos em
  `sistema/docs/`, cobrindo as decisões sobre número de agentes e
  estratégia de latência, decisões de usabilidade, defesa das escolhas
  de serviços AWS gerenciados e esta própria metodologia.

Essa infraestrutura documental atende diretamente ao RNF03
(Manutenibilidade), ao operacionalizar reprodutibilidade e rastreabilidade
em um nível que vai além do código-fonte, e sustenta o atendimento ao
RNF02 (Confiabilidade), ao ancorar as decisões arquiteturais em
documentos explícitos e auditáveis.
