## 3.10 Desenvolvimento da Solução

### 3.10.1 Visão Geral

A implementação da solução foi conduzida segundo uma abordagem incremental e
iterativa, na qual o sistema evoluiu em versões sucessivas à medida que as
hipóteses de arquitetura eram validadas pelos testes automatizados e pelos
critérios de aceitação derivados dos requisitos (seções 3.4.1 e 3.4.2). O
desenvolvimento seguiu o princípio da separação clara de responsabilidades,
de modo que cada componente possuísse um papel bem definido e pudesse ser
evoluído, substituído ou escalado de forma independente dos demais — em
alinhamento com o RNF03 (Manutenibilidade) e o RNF05 (Escalabilidade).

Para operacionalizar essa abordagem, foram adotadas as seguintes ferramentas
e práticas:

- **AWS CDK em Python** para descrever toda a infraestrutura como código,
  versionada no mesmo repositório da aplicação. Essa escolha permite
  reproduzir o ambiente em diferentes contas AWS a partir de um comando
  (`cdk deploy`), reduzindo o risco de desvios entre ambientes.
- **Python 3.11** como linguagem de backend, utilizada tanto nos agentes
  (Bedrock AgentCore Runtime) quanto nas funções Lambda que compõem as
  *tools* e os pipelines de processamento.
- **Next.js com TypeScript e Tailwind CSS** no frontend, hospedado
  estaticamente em Amazon S3 e distribuído por Amazon CloudFront com Origin
  Access Control.
- **Git e GitHub** para controle de versão, com histórico granular por
  *commit* a fim de garantir rastreabilidade das decisões técnicas e, quando
  necessário, reversão rápida.
- **Scripts auxiliares em Python**, mantidos no diretório `scripts/`, para
  operações que preservam estado e não cabem no fluxo do CDK — por exemplo,
  a construção e publicação da imagem do contêiner do agente no Amazon ECR
  por meio do script `configurar_agentcore.py`.
- **Diagramas em draw.io** para registrar a arquitetura em alto nível,
  mantidos como arquivos `.drawio` versionados junto ao código, de modo que
  a documentação visual acompanhe a evolução do sistema.

### 3.10.2 Estrutura Modular do Sistema

A solução foi organizada em cinco camadas lógicas, implementadas como
módulos independentes dentro de um mesmo repositório. Essa estrutura
modular, descrita a seguir, foi espelhada tanto na organização dos
diretórios do projeto quanto na organização dos *stacks* CDK, permitindo
que cada camada tivesse seu próprio ciclo de implantação.

#### 3.10.2.1 Frontend

A camada de interação com o usuário foi implementada como uma aplicação
*Single Page Application* construída em Next.js com TypeScript. A escolha
pelo Next.js levou em consideração a produtividade do *framework*, a
maturidade do ecossistema e a possibilidade de gerar artefatos estáticos
otimizados para hospedagem em S3.

O estilo visual foi padronizado via Tailwind CSS, utilizando a paleta
oficial do GEVI documentada no manual de identidade visual do projeto. As
principais telas planejadas foram cadastro, confirmação de e-mail, login,
recuperação de senha, *onboarding* (coleta do perfil de investidor),
*dashboard*, chat com o agente, investimentos, configurações, minha conta e
suporte — cada uma atendendo aos requisitos funcionais correspondentes
definidos em 3.4.1.

A entrega do frontend aos usuários é feita por meio da distribuição Amazon
CloudFront, com *Origin Access Control* (OAC) protegendo o bucket S3 de
origem contra acesso público direto. A comunicação com o backend ocorre
exclusivamente via chamadas HTTPS ao Amazon API Gateway, autenticadas por
*token* JWT emitido pelo Cognito.

#### 3.10.2.2 Autenticação e Gerenciamento de Usuários

Para atender ao RF01 (cadastro) e ao RF02 (autenticação), e cumprir os
controles de segurança previstos em RNF04, foi adotado o Amazon Cognito
como provedor de identidade. O Cognito *User Pool* centraliza o ciclo de
vida das contas — criação, confirmação por e-mail, autenticação,
recuperação de senha e revogação —, eliminando a necessidade de armazenar
senhas em componentes próprios do projeto.

A integração entre o frontend e o Cognito é feita por meio da biblioteca
AWS Amplify Auth, que abstrai os fluxos de SRP (*Secure Remote Password*)
e gerencia a renovação automática dos *tokens* no navegador. No backend, as
rotas protegidas no API Gateway são validadas por um *authorizer* JWT
configurado via CDK, de modo que apenas chamadas acompanhadas de um *token*
válido alcançam as Lambdas e os agentes.

As preferências do usuário (perfil de investidor, respostas do *onboarding*
e carteira declarada) são persistidas no Amazon DynamoDB em tabelas
separadas por domínio funcional, com atributo de particionamento baseado no
identificador do usuário (`sub` do Cognito).

#### 3.10.2.3 Agentes Inteligentes

A camada de agentes foi implementada sobre o Amazon Bedrock AgentCore
Runtime, serviço que fornece execução gerenciada de agentes conversacionais
com estado, incluindo primitivas para memória de curto prazo e identidade.
A escolha pelo AgentCore é justificada em detalhe na seção 3.9.

Foi projetado um conjunto de quatro agentes especializados, cada um
responsável por um subconjunto bem definido do processo de recomendação:

- **Orquestrador** — ponto de entrada único das conversas; interpreta a
  intenção do usuário, decide quais agentes especialistas e *tools* acionar
  e consolida a resposta final.
- **Agente de Risco** — avalia o perfil do investidor e calcula limites de
  alocação por classe de ativo.
- **Agente de Seleção** — propõe os ativos específicos que compõem a
  recomendação, respeitando os limites de risco.
- **Agente Explicador** — gera a justificativa em linguagem natural (XAI),
  amarrando a recomendação aos indicadores macroeconômicos e às
  preferências declaradas, atendendo ao RF07 (explicabilidade).

Cada agente é empacotado em um contêiner próprio, construído com Podman ou
Docker e publicado no Amazon ECR por meio do script `configurar_agentcore.py`.
A referência da imagem final é passada ao AgentCore via contexto do CDK,
permitindo que o *deploy* da infraestrutura seja independente da construção
da imagem.

Foi adotada uma estratégia de **modelo dual** na camada de inferência:
Claude Opus 4.5 é utilizado nos agentes que exigem maior capacidade de
raciocínio e geração de texto explicativo (Orquestrador e Explicador),
enquanto Claude Haiku é utilizado nos agentes com decisões mais
estruturadas e sensíveis à latência (Risco e Seleção).

#### 3.10.2.4 Tools e Pipelines de Processamento

Os agentes acionam capacidades externas por meio de *tools*, implementadas
como funções AWS Lambda em Python. Essa camada concentra toda a lógica
determinística do sistema — consultas a APIs externas, cálculos
quantitativos e consultas a bases de dados —, mantendo os agentes focados
em raciocínio e orquestração.

Foram planejadas sete funções:

- **fn-api-proxy** — fachada única entre o API Gateway e os demais
  componentes, aplicando normalizações de requisição e resposta.
- **fn-perfil-usuario** — operações sobre o perfil do investidor no
  DynamoDB.
- **fn-consulta-APIs** — consultas de preços de ativos e informações de
  mercado.
- **fn-consulta-indicadores** — leitura de indicadores macroeconômicos a
  partir da API do BACEN (SELIC, IPCA, câmbio).
- **fn-calculo-simulacao** — projeções de rentabilidade para a carteira
  recomendada.
- **fn-analise-portfolio** — análise da carteira atual do usuário.
- **fn-selecao-ativos** — composição algorítmica da carteira recomendada,
  respeitando o perfil de risco.

Para dar sustentação ao RF07 (explicabilidade) e ao RF08 (base de
conhecimento), foi configurada uma Knowledge Base do Amazon Bedrock, com
indexação sobre o Amazon OpenSearch Serverless e geração de *embeddings*
via Amazon Titan Text Embeddings V2. Essa base concentra documentos de
referência sobre produtos financeiros, glossário e políticas, servindo como
fonte citável nas respostas dos agentes.

Processos agendados — por exemplo, a atualização periódica de indicadores
macroeconômicos — foram previstos via Amazon EventBridge, acionando
Lambdas em janelas de tempo específicas sem a necessidade de componentes
*cron* externos.

#### 3.10.2.5 Camada de Segurança, Observabilidade e Compliance

Para atender ao RNF04 (Segurança), ao RNF02 (Confiabilidade) e ao RNF07
(Disponibilidade), foi projetada uma camada transversal que atravessa todos
os módulos da aplicação.

Na dimensão de **segurança**, o perímetro público da aplicação é protegido
por AWS WAF à frente do CloudFront e do API Gateway, com regras
gerenciadas e limitação de taxa; os dados em repouso são cifrados com
chaves CMK gerenciadas no AWS KMS; o tráfego é obrigatoriamente HTTPS/TLS;
e o acesso entre serviços é concedido por papéis IAM de menor privilégio.

Na dimensão de **observabilidade**, todas as Lambdas e os agentes emitem
logs estruturados em JSON no Amazon CloudWatch Logs e são rastreados por
AWS X-Ray, permitindo correlacionar uma requisição desde o frontend até a
chamada ao modelo. Métricas e alarmes são agregados em um *dashboard*
CloudWatch dedicado, com alarmes de erro e de latência configurados para
as Lambdas de produção.

Na dimensão de **governança e compliance**, foi habilitado o AWS
CloudTrail em modo multi-região, com logs direcionados a um bucket S3
dedicado de auditoria, versionado e com política de retenção. Essa
configuração oferece um registro imutável das operações realizadas na
conta, útil tanto para auditoria quanto para investigações de incidentes.

A camada foi organizada em *stacks* CDK separados — `security_stack`,
`data_stack`, `observability_stack`, entre outros — para evitar
acoplamento entre controles transversais e a lógica de negócio e permitir
sua evolução independente.

### 3.10.3 Funcionalidades Implementadas no Protótipo

O protótipo entregue nesta pesquisa contempla as funcionalidades definidas
em 3.4.1, com o escopo descrito a seguir:

- **RF01 — Cadastro de usuário**: formulário completo no frontend, com
  confirmação por e-mail via Cognito.
- **RF02 — Autenticação**: login com e-mail e senha, recuperação por
  e-mail e sessões autenticadas por JWT.
- **RF03 — Coleta do perfil de investidor**: fluxo de *onboarding* com
  questionário, armazenamento no DynamoDB e associação ao usuário
  autenticado.
- **RF04 — Chat com o agente**: interface conversacional com o
  Orquestrador, com histórico persistido na memória do AgentCore.
- **RF05 — Recomendação de carteira**: *pipeline* Risco → Seleção
  acionado pelo Orquestrador, retornando a alocação sugerida de forma
  estruturada.
- **RF06 — Simulação de rentabilidade**: cálculo projetivo via
  `fn-calculo-simulacao`, apresentado em formato tabular no frontend.
- **RF07 — Explicabilidade**: respostas do Agente Explicador
  contextualizadas por indicadores macroeconômicos e por documentos da
  Knowledge Base, com citação da fonte.
- **RF08 — Base de conhecimento**: Knowledge Base consultada pelos agentes
  para fundamentar respostas e definições.
- **RF09 — Dashboard do usuário**: painel com perfil, última recomendação
  e principais indicadores.
- **RF10 — Análise de carteira declarada**: funcionalidade de *upload* da
  carteira atual e análise via `fn-analise-portfolio`.

Os requisitos não funcionais são atendidos pelos mecanismos descritos ao
longo das camadas: o RNF02 (Confiabilidade) pelos testes automatizados e
pela observabilidade; o RNF03 (Manutenibilidade) pela modularidade e pela
infraestrutura como código; o RNF04 (Segurança) pelos controles descritos
em 3.10.2.5; o RNF05 (Escalabilidade) pelo uso de serviços *serverless*; e
o RNF07 (Disponibilidade) pela arquitetura *multi-AZ* nativa dos serviços
gerenciados utilizados.
