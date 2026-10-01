# Funções Lambda

Cada subpasta contém uma função Lambda em Python 3.12. O código comum fica em
`shared/` (na raiz do projeto) e é reutilizado por todas as funções.

## Funções

| Pasta | Aciona | Responsabilidade |
|---|---|---|
| `fn-api-proxy` | Frontend (HTTP API) | Ponte entre o frontend e o AgentCore: chat assíncrono, histórico, carteira e tools do Gateway |
| `fn-perfil-usuario` | Agente_Perfil | CRUD do Perfil_Investidor (DynamoDB `Users`) |
| `fn-consulta-APIs` | EventBridge (5 min) | Escreve indicadores (BCB/B3 → `EconomicIndicators`) |
| `fn-consulta-indicadores` | Agente_Macroeconomico | Lê indicadores e calcula variações |
| `fn-calculo-simulacao` | Agente_Risco | Volatilidade, risco e cenários de simulação |
| `fn-analise-portfolio` | Agente_Selecao_Ativos | Composição/diversificação do portfólio |
| `fn-selecao-ativos` | Agente_Selecao_Ativos | Filtra e ranqueia ativos por risco/retorno |

## Proxy (`fn-api-proxy`)

Fica atrás do HTTP API (autorizado pelo Cognito) e repassa o token do usuário
ao AgentCore. O `user_id` vem sempre do `sub` do JWT validado, nunca do corpo
enviado pelo cliente. Usa apenas a biblioteca padrão + `boto3`.

Rotas e ações (corpo JSON em `POST /chat` com o campo `acao`):

| Rota / ação | O que faz |
|---|---|
| `/chat` `acao=chat` | Cria um job e dispara o worker assíncrono; devolve `jobId` e `conversaId` |
| `/chat` `acao=status` | Consulta o resultado do job (polling) |
| `/chat` `acao=avaliar_perfil` | Avalia o questionário de suitability (síncrono) |
| `/chat` `acao=listar_conversas` / `obter_conversa` | Histórico de conversas (`ChatConversas`) |
| `/chat` `acao=listar_portfolio` / `salvar_portfolio` | Carteira do investidor (`Portfolios`) |
| `/gateway/{tool}` | Encaminha uma tool do Gateway (dados reais) |

O worker (invocação assíncrona) chama o Runtime, grava o resultado no job
(`ChatJobs`, com TTL) e persiste a resposta da IA no histórico da conversa. O
`salvar_portfolio` substitui a carteira do usuário (replace-all), valida o tipo
do ativo e deriva o percentual a partir do valor.

## Estrutura de empacotamento

Cada função é empacotada como um artefato ZIP contendo:

1. O `handler.py` da função (ponto de entrada `handler`).
2. O pacote `shared/` (copiado/vendorizado no build).
3. As dependências listadas no `requirements.txt` da função, quando houver.

> `boto3` já está disponível no runtime gerenciado da AWS Lambda e não precisa
> ser empacotado. Bibliotecas adicionais (ex.: `requests` em `fn-consulta-APIs`)
> devem ser vendorizadas no artefato.

O empacotamento efetivo (via AWS CDK) é definido a partir da Tarefa 2. Os
artefatos de build ficam em `lambdas/<fn>/build/` e são ignorados pelo Git.

## Convenção do handler

Todas as funções expõem `handler(event, context)` como ponto de entrada,
configurado na AWS Lambda como `handler.handler`.
