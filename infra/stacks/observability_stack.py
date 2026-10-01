"""Stack de observabilidade — Amazon CloudWatch (tarefa 11.2).

Cria métricas, alarmes e um dashboard de saúde do sistema sobre os sinais das
Lambdas, das tabelas DynamoDB e da plataforma AgentCore (Gateway e Runtime),
conforme o design.md ("Observabilidade"):

- Alarmes de taxa de erro e throttling das 6 Lambdas.
- Alarmes de erros de sistema e throttling das 4 tabelas DynamoDB.
- Alarmes de erro/throttle do Runtime do AgentCore (quando provisionado).
- Dashboard com invocações, erros, latência (Lambdas/Runtime), throttling e
  tempo de execução das tools no Gateway.

Os logs estruturados em JSON (tarefa 11.1) são configurados no código/definição
das Lambdas (``logging_format = JSON`` na ComputeStack); esta stack define os
alarmes e o painel sobre esses sinais.

Recebe as referências por parâmetro (dependências one-directional a partir de
Compute, Data e Agents), sem criar ciclos entre stacks.

Requisitos cobertos: 11.1, 11.2, 14.1, 14.4.
"""

from __future__ import annotations

from aws_cdk import Duration, Stack
from aws_cdk import aws_bedrockagentcore as agentcore
from aws_cdk import aws_cloudwatch as cw
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_lambda as lambda_
from constructs import Construct

from config import ConfigAmbiente

# Janela de avaliação padrão das métricas de alarme.
PERIODO = Duration.minutes(5)

# Limiar de latência (p99) das Lambdas exibido/alarmado (ms). Heurística do
# protótipo: acima disso, investigar (as tools devem responder rápido).
LIMIAR_DURACAO_MS = 10_000

# Operações DynamoDB monitoradas nos alarmes. As métricas ``*_for_operations``
# geram uma expressão matemática combinando uma métrica por operação; um alarme
# aceita no máximo 10 métricas, então restringimos às operações efetivamente
# usadas pelas Lambdas (get/put/update/delete/query/scan).
OPERACOES_MONITORADAS = (
    dynamodb.Operation.GET_ITEM,
    dynamodb.Operation.PUT_ITEM,
    dynamodb.Operation.UPDATE_ITEM,
    dynamodb.Operation.DELETE_ITEM,
    dynamodb.Operation.QUERY,
    dynamodb.Operation.SCAN,
)


class ObservabilityStack(Stack):
    """Provisiona alarmes e o dashboard de saúde do sistema (CloudWatch).

    Atributos:
        alarmes: lista de todos os ``cloudwatch.Alarm`` criados.
        dashboard: o ``cloudwatch.Dashboard`` de saúde do sistema.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        config: ConfigAmbiente,
        funcoes: dict[str, lambda_.IFunction] | None = None,
        tabelas: dict[str, dynamodb.ITable] | None = None,
        gateway: agentcore.IGateway | None = None,
        runtime: agentcore.Runtime | None = None,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.config = config
        self._funcoes = funcoes or {}
        self._tabelas = tabelas or {}
        self._gateway = gateway
        self._runtime = runtime

        self.alarmes: list[cw.Alarm] = []
        self._criar_alarmes_lambdas()
        self._criar_alarmes_tabelas()
        self._criar_alarmes_runtime()
        self.dashboard = self._criar_dashboard()

    def _alarme(
        self,
        id_alarme: str,
        metrica: cw.IMetric,
        *,
        threshold: float,
        descricao: str,
        evaluation_periods: int = 1,
    ) -> cw.Alarm:
        """Cria um alarme padrão (>= threshold) e o registra em ``self.alarmes``.

        ``treat_missing_data = NOT_BREACHING``: ausência de dados (ex.: função
        sem invocações na janela) não dispara o alarme, evitando falso-positivo.
        """
        alarme = cw.Alarm(
            self,
            id_alarme,
            alarm_name=self.config.nome_recurso(id_alarme),
            metric=metrica,
            threshold=threshold,
            evaluation_periods=evaluation_periods,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
            alarm_description=descricao,
        )
        self.alarmes.append(alarme)
        return alarme

    def _criar_alarmes_lambdas(self) -> None:
        """Alarmes de erros e throttling para cada uma das 6 Lambdas."""
        for nome, funcao in self._funcoes.items():
            sufixo = "".join(p.capitalize() for p in nome.replace("fn-", "").split("-"))
            self._alarme(
                f"Alarme{sufixo}Erros",
                funcao.metric_errors(period=PERIODO),
                threshold=1,
                descricao=f"Erros na Lambda {nome} (>=1 em 5 min).",
            )
            self._alarme(
                f"Alarme{sufixo}Throttles",
                funcao.metric_throttles(period=PERIODO),
                threshold=1,
                descricao=f"Throttling na Lambda {nome} (>=1 em 5 min).",
            )

    def _criar_alarmes_tabelas(self) -> None:
        """Alarmes de erros de sistema e throttling para as tabelas DynamoDB."""
        for nome, tabela in self._tabelas.items():
            sufixo = nome.capitalize()
            self._alarme(
                f"AlarmeTabela{sufixo}SystemErrors",
                tabela.metric_system_errors_for_operations(
                    operations=list(OPERACOES_MONITORADAS), period=PERIODO
                ),
                threshold=1,
                descricao=f"Erros de sistema na tabela {nome} (>=1 em 5 min).",
            )
            self._alarme(
                f"AlarmeTabela{sufixo}Throttled",
                tabela.metric_throttled_requests_for_operation(
                    "Query", period=PERIODO
                ),
                threshold=1,
                descricao=(
                    f"Requisições Query com throttling na tabela {nome} "
                    "(>=1 em 5 min)."
                ),
            )

    def _criar_alarmes_runtime(self) -> None:
        """Alarmes de erro e throttling do Runtime do AgentCore (se existir)."""
        if self._runtime is None:
            return
        self._alarme(
            "AlarmeRuntimeErros",
            self._runtime.metric_total_errors(period=PERIODO),
            threshold=1,
            descricao="Erros totais no Runtime dos agentes (>=1 em 5 min).",
        )
        self._alarme(
            "AlarmeRuntimeThrottles",
            self._runtime.metric_throttles(period=PERIODO),
            threshold=1,
            descricao="Throttling no Runtime dos agentes (>=1 em 5 min).",
        )

    def _criar_dashboard(self) -> cw.Dashboard:
        """Monta o dashboard de saúde do sistema em tempo real (design.md).

        Linhas de widgets: invocações e erros das Lambdas, latência (p99) das
        Lambdas, throttling das Lambdas, sinais das tabelas DynamoDB, tempo de
        execução das tools no Gateway e latência/erros do Runtime (quando há).
        """
        dashboard = cw.Dashboard(
            self,
            "DashboardSaude",
            dashboard_name=self.config.nome_recurso("maia-saude"),
        )

        invocacoes = [f.metric_invocations(period=PERIODO) for f in self._funcoes.values()]
        erros = [f.metric_errors(period=PERIODO) for f in self._funcoes.values()]
        duracoes = [
            f.metric_duration(period=PERIODO, statistic="p99")
            for f in self._funcoes.values()
        ]
        throttles = [f.metric_throttles(period=PERIODO) for f in self._funcoes.values()]

        if invocacoes:
            dashboard.add_widgets(
                cw.GraphWidget(
                    title="Lambdas — invocações", left=invocacoes, width=12
                ),
                cw.GraphWidget(title="Lambdas — erros", left=erros, width=12),
            )
            dashboard.add_widgets(
                cw.GraphWidget(
                    title=f"Lambdas — latência p99 (ms, alerta > {LIMIAR_DURACAO_MS})",
                    left=duracoes,
                    left_annotations=[
                        cw.HorizontalAnnotation(value=LIMIAR_DURACAO_MS, label="limiar")
                    ],
                    width=12,
                ),
                cw.GraphWidget(title="Lambdas — throttling", left=throttles, width=12),
            )

        if self._tabelas:
            dashboard.add_widgets(
                cw.GraphWidget(
                    title="DynamoDB — throttling (Query)",
                    left=[
                        t.metric_throttled_requests_for_operation(
                            "Query", period=PERIODO
                        )
                        for t in self._tabelas.values()
                    ],
                    width=12,
                ),
                cw.GraphWidget(
                    title="DynamoDB — erros de usuário (4xx)",
                    left=[
                        t.metric_user_errors(period=PERIODO)
                        for t in self._tabelas.values()
                    ],
                    width=12,
                ),
            )

        # AgentCore: tempo de execução das tools (Gateway) e latência/erros do
        # Runtime, quando provisionados.
        agentcore_widgets: list[cw.IWidget] = []
        if self._gateway is not None:
            agentcore_widgets.append(
                cw.GraphWidget(
                    title="Gateway — tempo de execução das tools",
                    left=[self._gateway.metric_target_execution_time(period=PERIODO)],
                    width=12,
                )
            )
        if self._runtime is not None:
            agentcore_widgets.append(
                cw.GraphWidget(
                    title="Runtime — latência e erros",
                    left=[self._runtime.metric_latency(period=PERIODO)],
                    right=[self._runtime.metric_total_errors(period=PERIODO)],
                    width=12,
                )
            )
        if agentcore_widgets:
            dashboard.add_widgets(*agentcore_widgets)

        return dashboard
