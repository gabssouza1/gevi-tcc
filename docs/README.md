# Documentação de decisões — GEVI

Documentos de referência para a defesa do TCC. Cada um cobre um eixo
distinto das decisões tomadas durante o desenvolvimento.

## Estrutura

| Documento | Tema | Uso na defesa |
|---|---|---|
| [`decisoes-agentes-e-latencia.md`](decisoes-agentes-e-latencia.md) | Arquitetura de agentes, otimização de latência, decisões de LLM, `toolChoice` como restrição estrutural | Justificativa técnica de desempenho |
| [`decisoes-usabilidade.md`](decisoes-usabilidade.md) | Melhorias de UX/UI, correção de bugs de percepção, princípios de design | Justificativa da experiência do usuário |
| [`defesa-agentcore-bedrock.md`](defesa-agentcore-bedrock.md) | Justificativa da escolha do Amazon Bedrock e AgentCore em vez de alternativas | Defesa técnica das ferramentas |
| [`defesa-kiro-e-ai-dlc.md`](defesa-kiro-e-ai-dlc.md) | Justificativa do Kiro como IDE e do AI-DLC como metodologia de desenvolvimento | Defesa metodológica e de processo |

## Como os três se relacionam

Os três documentos são complementares e frequentemente se referenciam
mutuamente:

- Um bug técnico (memória do agente retornando dados obsoletos) tem
  causa raiz em **agentes-e-latencia** (o modelo ignora instruções de
  prompt; `toolChoice` resolveu), sintoma em **usabilidade** ("a IA
  responde com dados errados sobre a carteira"), e depende de uma
  capacidade específica do **AgentCore/Bedrock** (`toolConfig.toolChoice`
  na Converse API).
- A reorganização do Dashboard (em **usabilidade**) só faz sentido
  contra o feedback de progresso incremental (em **agentes-e-latencia**),
  que só é viável pela integração AgentCore Runtime ↔ DynamoDB ↔
  Lambda proxy (em **agentcore-bedrock**).

## Como usar em cada seção do TCC

- **Metodologia de desenvolvimento**: `defesa-kiro-e-ai-dlc.md` inteiro
  (seções 1–4 explicam o método; seção 3 mapeia como as fases do AI-DLC
  foram aplicadas ao GEVI)
- **Fundamentação teórica / arquitetura**: seções 1–4 de
  `defesa-agentcore-bedrock.md`
- **Requisitos não funcionais (desempenho)**: `decisoes-agentes-e-latencia.md`
  inteiro
- **Requisitos não funcionais (usabilidade)**: `decisoes-usabilidade.md`
  inteiro
- **Discussão de trade-offs e ameaças à validade**: seções finais de
  cada documento (todas terminam com "Trade-offs aceitos"); a seção 6 de
  `defesa-kiro-e-ai-dlc.md` é especialmente honesta sobre o que foi
  simplificado por causa do escopo de TCC
- **Conclusões e trabalhos futuros**: nível 2 de streaming (docs 1 e 2),
  histórico de carteira em tabela dedicada (doc 2), redução de custo do
  Explicador (doc 1), formalização do AI Operating Model (doc 4)
