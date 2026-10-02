# Textos para incluir a defesa do Kiro no TCC

Este arquivo reúne três ajustes a aplicar no corpo do TCC, prontos para copiar e colar no documento final (Word / Google Docs).

---

## AJUSTE 1 — Tabela 3 (seção 3.9 Tecnologias e Ferramentas)

**Localizar a linha atual:**

| Ambiente de desenvolvimento | Visual Studio Code | Ambiente utilizado para desenvolvimento e manutenção do sistema. |

**Substituir por:**

| Ambiente de desenvolvimento | Kiro IDE | Ambiente de desenvolvimento agentic baseado em VS Code, utilizado para codificação, versionamento de especificações (specs), definição de convenções do projeto (steering files) e automações do fluxo de desenvolvimento (hooks). |

*Opcional:* manter também a linha do Visual Studio Code como editor complementar, caso tenham sido usados em paralelo:

| Editor complementar | Visual Studio Code | Editor utilizado pontualmente para edição de arquivos auxiliares. |

---

## AJUSTE 2 — Nova subseção 3.10.3 (Metodologia de Desenvolvimento Assistido por IA)

Inserir entre a atual 3.10.2 (Estrutura Modular do Sistema) e a atual 3.10.3 (Funcionalidades Implementadas no Protótipo), renumerando as subseções seguintes.

### 3.10.3 Metodologia de Desenvolvimento Assistido por IA

O desenvolvimento do GEVI foi conduzido com apoio de ferramentas de inteligência artificial para codificação, revisão e automação, dentro do modelo incremental descrito na seção 3.5. Esta subseção descreve as duas peças centrais dessa prática: o Kiro, utilizado como IDE de desenvolvimento, e o AI-DLC (AI Development Lifecycle), consultado como framework de referência para a organização do trabalho.


É importante registrar, antes de detalhar essas peças, que elas foram adotadas como **ferramentas de produtividade da equipe**, não como substituição do trabalho dos autores. Toda decisão de projeto, revisão de código, análise de causa raiz de defeitos e redação dos artefatos do TCC permaneceu sob responsabilidade dos autores. A IA foi utilizada para acelerar tarefas mecânicas (geração de código a partir de especificações já aprovadas, refatorações, escrita de testes para invariantes identificadas), com revisão humana obrigatória nos pontos sensíveis.

#### 3.10.3.1 Kiro como ambiente de desenvolvimento

O Kiro é um ambiente de desenvolvimento integrado (IDE) construído sobre o Visual Studio Code, no qual o agente de IA é tratado como componente nativo do fluxo de trabalho. Diferentemente de assistentes de código tradicionais, nos quais a IA aparece como funcionalidade lateral (autocomplete ou chat em painel separado), o Kiro oferece primitivas estruturadas que persistem no projeto e permanecem versionadas junto ao código.

Quatro primitivas do Kiro foram utilizadas no desenvolvimento do GEVI:

- **Specs**: documentos estruturados de requisitos, projeto e tarefas, armazenados em `.kiro/specs/`. Permitiram que o contexto completo do sistema estivesse disponível a cada sessão de trabalho, sem necessidade de reexplicar o projeto a cada nova interação com o agente.
- **Steering files**: arquivos em `.kiro/steering/` que descrevem convenções do projeto (padrões AWS, práticas de segurança, estilo de código, fluxo de commits). São injetados automaticamente no contexto do agente, funcionando como "constituição" do projeto e evitando que as convenções sejam esquecidas entre sessões.
- **Hooks**: automações declaradas em `.kiro/hooks/` que reagem a eventos do IDE, como salvar arquivos ou enviar prompts. Foram utilizadas para executar verificações automatizadas, como testes de regressão em pontos específicos do desenvolvimento.
- **Suporte a Model Context Protocol (MCP)**: integração nativa com servidores externos de contexto, permitindo que documentação oficial e ferramentas internas pudessem ser consultadas pelo agente sem alternância manual entre janelas.


A escolha do Kiro em detrimento de alternativas (Visual Studio Code com GitHub Copilot, Cursor ou Claude Code) justifica-se, no contexto do GEVI, por três fatores. Primeiro, o volume de componentes distintos do sistema — frontend, backend serverless, infraestrutura como código, agentes de IA e integrações — exigia um ambiente capaz de manter o contexto do projeto como um todo, não apenas do arquivo em edição. Segundo, o projeto se estendeu ao longo de meses: sem o versionamento das specs e dos steering files, cada retomada partiria do zero em relação às convenções já estabelecidas. Terceiro, o próprio produto desenvolvido (sistema multiagente com IA generativa) alinha-se às primitivas do Kiro, que foi concebido para desenvolvimento com apoio de agentes.

#### 3.10.3.2 AI-DLC como framework de referência

O AI-DLC (AI Development Lifecycle) é um framework metodológico que organiza o desenvolvimento assistido por IA em quatro marcos sequenciais (M1 Align & Configure, M2 Prove Value, M3 Adapt & Integrate, M4 Scale & Handover), com pontos de verificação humana (Human-In-The-Loop ou HITL) nos momentos de decisão crítica e com rastreabilidade entre requisitos, decisões arquiteturais e código.

O AI-DLC foi consultado como **referência de método**, não aplicado em sua forma canônica. O framework foi originalmente projetado para engajamentos consultivos de doze semanas com equipes de seis ou mais pessoas e inclui atividades (assessment multi-persona de maturidade, workshops com sponsor executivo, planos formais de Organizational Change Management) incompatíveis com o escopo de um TCC. Foi explicitamente adotado o que fazia sentido para a escala do projeto:

- **Vocabulário de fases (M1 a M4)**: utilizado para organizar mentalmente o trabalho em definição do problema, prova de valor, adaptação às convenções e empacotamento. Esse vocabulário é compatível com o modelo incremental descrito na seção 3.5 e não o substitui.
- **Princípio de HITL em pontos críticos**: toda mudança que afetasse comportamento do modelo em produção, infraestrutura da conta AWS ou código de segurança passou por revisão humana obrigatória antes da aplicação. Esse princípio foi aplicado também pelo próprio Kiro, que oferece dois modos de autonomia: *autopilot* para tarefas de baixo risco e *supervised* para operações que exigem aprovação explícita.
- **Rastreabilidade requisito ↔ código**: comentários no código referenciando requisitos (por exemplo, `# Req 7.3` em `fn-consulta-indicadores`) e testes de propriedade nomeados por invariante do design (por exemplo, `test_prop06_e_pipeline.py`) estabelecem uma matriz implícita de rastreabilidade, que permite a qualquer leitor identificar qual trecho de código atende a qual requisito.


Não foram adotados do AI-DLC: assessments formais de maturidade, oficinas com sponsor externo, OCM com enablement de champions e as sete métricas canônicas do framework em sua totalidade (foram priorizadas as métricas de latência de resposta e cobertura de testes, por serem as mais relevantes aos requisitos não funcionais do GEVI).

#### 3.10.3.3 Evidências e trade-offs

A adoção dessas práticas deixou traços verificáveis no repositório: as specs em `.kiro/specs/multiagent-investment-advisor/`, os arquivos de convenção em `.kiro/steering/`, os hooks de automação em `.kiro/hooks/` e a suíte de testes de propriedade em `sistema/tests/test_prop*.py` constituem evidência concreta tanto do uso do Kiro quanto da aplicação dos princípios metodológicos descritos.

Entre os trade-offs assumidos, destaca-se a dependência de uma ferramenta em evolução: o Kiro é um IDE relativamente novo, e mudanças futuras de sua API ou comportamento poderiam exigir ajustes. Essa dependência é mitigada pelo fato de os artefatos essenciais (specs, steering e hooks) serem arquivos em formato texto, versionados junto ao código, o que preserva a legibilidade e a portabilidade do projeto para outros ambientes de desenvolvimento.

Em síntese, a combinação do Kiro como IDE e do AI-DLC como framework de referência deu estrutura ao desenvolvimento sem substituir o modelo incremental já adotado. O resultado é um projeto cuja construção é reproduzível e cujas decisões técnicas estão rastreáveis em documentação versionada, atendendo aos requisitos de manutenibilidade (RNF03) e escalabilidade (RNF05) estabelecidos na seção 3.4.6.

---

## AJUSTE 3 — Gancho opcional no Capítulo 4.1 (Visão Geral da Solução)

**Adicionar ao final do segundo parágrafo da seção 4.1**, após a frase que termina com "AWS Cloud Development Kit (CDK) em Python.":

> O desenvolvimento foi conduzido no Kiro, ambiente de desenvolvimento assistido por IA, com o framework AI-DLC utilizado como referência metodológica. A descrição dessas práticas e a sua articulação com o modelo incremental adotado estão detalhadas na seção 3.10.3.

Esse gancho é opcional. Serve apenas para que a banca, ao ler o Capítulo 4, saiba onde encontrar a defesa metodológica se desejar aprofundar.

---

## Resumo de aplicação

| Onde | O que fazer | Obrigatoriedade |
|---|---|---|
| Tabela 3 (seção 3.9) | Substituir "Visual Studio Code" por "Kiro IDE" na linha de ambiente de desenvolvimento | Obrigatório, se o projeto foi feito no Kiro |
| Seção 3.10 (nova 3.10.3) | Inserir a subseção Metodologia de Desenvolvimento Assistido por IA | Recomendado, principal lugar de defesa |
| Seção 4.1 | Adicionar o gancho de uma frase remetendo à 3.10.3 | Opcional |

