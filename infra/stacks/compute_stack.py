"""Stack de compute — as 6 funções Lambda do backend.

Implementa as tarefas 5.x e 6.x do plano (provisionamento CDK das funções cujo
código-fonte já vive em ``lambdas/`` e é reutilizado por ``shared/``):

- ``fn-perfil-usuario``       (tarefa 5.1) — CRUD do Perfil_Investidor.
- ``fn-consulta-APIs``        (tarefa 5.3) — pipeline assíncrono, apenas escrita.
- ``fn-consulta-indicadores`` (tarefa 5.7) — apenas leitura de indicadores.
- ``fn-calculo-simulacao``    (tarefa 6.1) — volatilidade, risco e cenários.
- ``fn-analise-portfolio``    (tarefa 6.4) — composição/diversificação.
- ``fn-selecao-ativos``       (tarefa 6.6) — filtra e ranqueia ativos.

Todas em Python 3.12, empacotadas a partir de ``lambdas/<fn>/`` com o pacote
comum ``shared/`` vendorizado (ver ``infra/empacotamento.py``). Cada função usa
a role de execução de menor privilégio criada na ``DataStack`` (tarefa 4.3) e
recebe por variável de ambiente os nomes físicos (com prefixo de ambiente) das
tabelas/bucket que acessa, casando com os nomes que os handlers leem de
``os.environ`` (ex.: ``TABELA_USERS``, ``TABELA_INDICADORES``).

As funções são expostas em ``self.funcoes`` (``{nome: Function}``) para o wiring
posterior: os ``GatewayTarget`` do AgentCore (tarefa 9.2), o alvo da regra
EventBridge (tarefa 12.1) e as métricas/alarmes do CloudWatch (tarefa 11.2).

Logs estruturados (tarefa 11.1): as funções são criadas com
``logging_format = JSON`` para que os registros cheguem ao CloudWatch já em
JSON, com nível de log de aplicação ``INFO``.

Requisitos cobertos pela camada: 1.x, 3.x, 4.x, 6.x, 7.x, 8.x, 9.x, 14.2, 14.4.
"""

from __future__ import annotations

from aws_cdk import Duration, Stack
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from constructs import Construct

from config import ConfigAmbiente
from empacotamento import preparar_codigo_lambda

# Runtime único das 6 funções (design.md — Python 3.12).
RUNTIME = lambda_.Runtime.PYTHON_3_12

# Ponto de entrada padrão dos handlers (ver lambdas/README.md).
HANDLER = "handler.handler"

# Timeout padrão das tools acionadas por agente (respostas síncronas rápidas).
TIMEOUT_PADRAO = Duration.seconds(30)

# Timeout maior do pipeline assíncrono: fn-consulta-APIs faz egress HTTPS para
# BCB/B3 e pode enfrentar latência/retry das APIs externas.
TIMEOUT_PIPELINE = Duration.seconds(60)

# Memória padrão (MB). Cargas são leves (I/O em DynamoDB/S3 e cálculos simples).
MEMORIA_PADRAO_MB = 256


class ComputeStack(Stack):
    """Provisiona as 6 funções Lambda do backend (tarefas 5.x/6.x).

    Recebe da ``DataStack`` as tabelas, o bucket e as roles de menor privilégio
    e cria as funções ligando cada handler à sua role e às variáveis de ambiente
    correspondentes.

    Atributos:
        funcoes: Dicionário ``{nome_da_funcao: aws_lambda.Function}`` com as 6
            funções, consumido pelas tarefas 9.2 (GatewayTargets) e 11.2
            (métricas/alarmes).
        regra_indicadores: Regra EventBridge (a cada 5 min) que aciona
            ``fn-consulta-APIs`` — o pipeline assíncrono de indicadores (wiring
            da tarefa 12.1, co-localizado com a função para evitar ciclo entre
            stacks).
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        config: ConfigAmbiente,
        tabela_users: dynamodb.ITable,
        tabela_indicadores: dynamodb.ITable,
        tabela_portfolios: dynamodb.ITable,
        tabela_historico: dynamodb.ITable,
        bucket_investimentos: s3.IBucket,
        roles_lambdas: dict[str, iam.IRole],
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.config = config

        # Ambiente por função: nomes físicos (com prefixo) das tabelas/bucket que
        # cada handler lê de os.environ. Chaves idênticas às usadas nos handlers.
        ambiente_por_funcao: dict[str, dict[str, str]] = {
            "fn-perfil-usuario": {
                "TABELA_USERS": tabela_users.table_name,
            },
            "fn-consulta-APIs": {
                "TABELA_INDICADORES": tabela_indicadores.table_name,
                "TABELA_USERS": tabela_users.table_name,
            },
            "fn-consulta-indicadores": {
                "TABELA_INDICADORES": tabela_indicadores.table_name,
            },
            "fn-calculo-simulacao": {
                "TABELA_INDICADORES": tabela_indicadores.table_name,
                "TABELA_HISTORICO": tabela_historico.table_name,
            },
            "fn-analise-portfolio": {
                "TABELA_PORTFOLIOS": tabela_portfolios.table_name,
                "BUCKET_INVESTIMENTOS": bucket_investimentos.bucket_name,
            },
            "fn-selecao-ativos": {
                "TABELA_INDICADORES": tabela_indicadores.table_name,
                "TABELA_USERS": tabela_users.table_name,
                "TABELA_HISTORICO": tabela_historico.table_name,
            },
        }

        # fn-consulta-APIs é o único pipeline assíncrono (timeout maior).
        timeout_por_funcao = {"fn-consulta-APIs": TIMEOUT_PIPELINE}

        self.funcoes: dict[str, lambda_.Function] = {}
        for nome_fn in config.nomes.lambdas:
            self.funcoes[nome_fn] = self._criar_funcao(
                nome_fn,
                role=roles_lambdas[nome_fn],
                ambiente=ambiente_por_funcao[nome_fn],
                timeout=timeout_por_funcao.get(nome_fn, TIMEOUT_PADRAO),
            )

        # Pipeline assíncrono de indicadores (tarefa 4.2 + wiring da 12.1): regra
        # EventBridge a cada 5 min acionando fn-consulta-APIs. A regra é
        # co-localizada com a função (ambas nesta stack) porque a ComputeStack já
        # depende da DataStack; criar a regra na DataStack e apontá-la para a
        # função da ComputeStack criaria um ciclo Data<->Compute.
        self.regra_indicadores = events.Rule(
            self,
            "RegraIndicadores",
            rule_name=self.config.nome_recurso(config.nomes.regra_indicadores),
            description=(
                "Pipeline assincrono de indicadores: aciona fn-consulta-APIs a "
                "cada 5 minutos (design.md — Pipeline Assincrono de Indicadores)."
            ),
            schedule=events.Schedule.rate(Duration.minutes(5)),
        )
        self.regra_indicadores.add_target(
            targets.LambdaFunction(self.funcoes["fn-consulta-APIs"])
        )

    def _id_construct(self, nome_fn: str) -> str:
        """Converte ``fn-perfil-usuario`` no CamelCase do construct ID (estável)."""
        return "".join(parte.capitalize() for parte in nome_fn.split("-"))

    def _criar_funcao(
        self,
        nome_fn: str,
        *,
        role: iam.IRole,
        ambiente: dict[str, str],
        timeout: Duration,
    ) -> lambda_.Function:
        """Cria uma função Lambda (Python 3.12) a partir de ``lambdas/<fn>/``.

        - ``code``: diretório de artefato montado por ``preparar_codigo_lambda``
          (handler + ``shared`` vendorizado + dependências do ``requirements.txt``
          da função, quando houver — ex.: ``requests`` em ``fn-consulta-APIs``).
        - ``role``: role de execução de menor privilégio da ``DataStack`` (4.3),
          já com as permissões de dados e uso da chave KMS que a função precisa.
        - ``environment``: nomes físicos (com prefixo de ambiente) das
          tabelas/bucket, casando com o que o handler lê de ``os.environ``.
        - ``logging_format = JSON``: logs estruturados no CloudWatch (tarefa 11.1).
        - ``tracing = ACTIVE``: rastros do X-Ray para latência/erros (observabilidade).
        - ``log_retention``: retém os logs por tempo limitado para conter custo.

        O nome físico da função recebe o prefixo de ambiente de ``config``
        (ex.: ``dev-fn-perfil-usuario``).
        """
        return lambda_.Function(
            self,
            self._id_construct(nome_fn),
            function_name=self.config.nome_recurso(nome_fn),
            runtime=RUNTIME,
            handler=HANDLER,
            code=lambda_.Code.from_asset(preparar_codigo_lambda(nome_fn)),
            role=role,
            environment=ambiente,
            timeout=timeout,
            memory_size=MEMORIA_PADRAO_MB,
            logging_format=lambda_.LoggingFormat.JSON,
            application_log_level_v2=lambda_.ApplicationLogLevel.INFO,
            tracing=lambda_.Tracing.ACTIVE,
            log_retention=logs.RetentionDays.ONE_MONTH,
            description=(
                f"Funcao {nome_fn} do Sistema Multiagente de Investimentos "
                f"(ambiente {self.config.nome})."
            ),
        )
