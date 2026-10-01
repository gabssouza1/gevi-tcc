"""Stack dos agentes — Amazon Bedrock AgentCore.

Provisiona, via IaC, a plataforma do AgentCore que sustenta os 6 agentes — um
Agente_Orquestrador (LLM) e 5 agentes especialistas que ele aciona (ver
``infra/AGENTCORE_IAC.md`` — tarefa 9.1). Cobre:

- Tarefa 9.2 — Gateway com validação de JWT do Cognito na entrada e roteamento
  de tools (um ``GatewayTarget`` por Lambda de tool).
- Tarefa 9.3 — Identity: isolamento por agente (uma ``WorkloadIdentity`` por
  agente) conforme a matriz de tools do design.
- Tarefa 9.5 — Memory: short-term (TTL configurável) e long-term, com KMS.
- Tarefa 9.6 — Runtime/RuntimeEndpoint que hospeda a imagem dos agentes (a
  imagem em si é um artefato de build, publicada por script CLI — ver doc).

Escopo de tools (design.md — "Controle de Acesso por Agente"): são registradas
as **5 Lambdas acionadas por agente**. A sexta função, ``fn-consulta-APIs``, é o
pipeline assíncrono do EventBridge (apenas escrita — Propriedade 10) e NÃO é uma
tool de agente, portanto não vira ``GatewayTarget`` (seu alvo é a regra
EventBridge, ligada na tarefa 12.1).

Knowledge Base: o AgentCore Gateway não expõe um tipo de target de Knowledge
Base (os tipos suportados são Lambda/OpenAPI/Smithy/MCP). Por isso o KB é
consumido pelo Agente_Explicador como capacidade nativa de RAG do Runtime
(Bedrock Retrieve), com a role do Runtime autorizada no KB (tarefa 9.6), em vez
de um GatewayTarget. Isso refina a redação do diagrama do design sem alterar o
comportamento: o Explicador continua sendo o único consumidor do KB.

Requisitos cobertos pela camada: 2.1, 2.3, 3.x, 5.x, 9.3, 10.x, 12.2, 13.1.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from aws_cdk import Duration, RemovalPolicy, Stack
from aws_cdk import aws_bedrockagentcore as agentcore
from aws_cdk import aws_cognito as cognito
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from constructs import Construct

from config import ConfigAmbiente

# Modelos de linguagem usados pelos agentes (design.md — Stack de LLM), via
# inference profile (cross-region). O Opus é usado pelo Agente_Orquestrador e
# pelo Agente_Explicador (decisão e redação da resposta final); o Haiku é
# usado pelos agentes que só formatam notas internas concisas (Agente_Risco,
# Agente_Selecao_Ativos) — ver ``agentcore/agentes.py`` (MODELO_LLM /
# MODELO_LLM_RAPIDO). Os inference profiles do Claude roteiam entre
# us-east-1/us-east-2/us-west-2; a policy IAM autoriza o profile e os
# foundation models subjacentes nas três regiões.
MODELO_LLM = "us.anthropic.claude-opus-4-5-20251101-v1:0"
MODELO_LLM_RAPIDO = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
_REGIOES_INFERENCE_PROFILE = ("us-east-1", "us-east-2", "us-west-2")

# Chave de contexto do CDK com a URI da imagem de contêiner dos agentes (ECR),
# publicada por ``scripts/configurar_agentcore.py``. Quando ausente, o Runtime
# não é criado (ver _criar_runtime), evitando falha de deploy por imagem
# inexistente — a plataforma (Gateway/Identity/Memory) é provisionada mesmo assim.
CONTEXTO_IMAGEM_AGENTES = "imagem_agentes"


@dataclass(frozen=True)
class EspecTool:
    """Especificação de uma tool (Lambda) roteada pelo Gateway.

    Atributos:
        nome_funcao: chave da função em ``ComputeStack.funcoes`` (ex.:
            ``fn-perfil-usuario``).
        agente: agente do design que aciona a tool (rastreabilidade + Identity).
        nome_tool: nome lógico da tool exposto ao agente pelo Gateway.
        descricao: descrição da tool para o LLM decidir quando usá-la.
        propriedades: mapa ``campo → (tipo, descrição)`` do schema de entrada.
        obrigatorios: campos obrigatórios do schema de entrada.
    """

    nome_funcao: str
    agente: str
    nome_tool: str
    descricao: str
    propriedades: dict[str, tuple[str, str]]
    obrigatorios: tuple[str, ...] = field(default_factory=tuple)


# Matriz de tools por agente (design.md — "Controle de Acesso por Agente"),
# com os campos de entrada alinhados ao que cada handler lê do ``event``.
MATRIZ_TOOLS: tuple[EspecTool, ...] = (
    EspecTool(
        nome_funcao="fn-perfil-usuario",
        agente="Agente_Perfil",
        nome_tool="gerenciar_perfil_investidor",
        descricao=(
            "CRUD do perfil do investidor (criar, ler, atualizar ou remover) na "
            "base de usuários. Retorna o perfil com tolerância a risco e nível "
            "de conhecimento."
        ),
        propriedades={
            "operacao": ("STRING", "Operação: criar, ler, atualizar ou remover."),
            "user_id": ("STRING", "Identificador do usuário."),
            "perfil": ("OBJECT", "Dados do perfil (para criar/atualizar)."),
        },
        obrigatorios=("operacao",),
    ),
    EspecTool(
        nome_funcao="fn-consulta-indicadores",
        agente="Agente_Macroeconomico",
        nome_tool="consultar_indicadores_economicos",
        descricao=(
            "Lê os indicadores macroeconômicos mais recentes (Selic, IPCA, "
            "dólar, CDI) já persistidos e calcula variações. Apenas leitura."
        ),
        propriedades={
            "indicadores": (
                "ARRAY",
                "Lista de indicadores a consultar (padrão: todos).",
            ),
            "max_idade_horas": (
                "NUMBER",
                "Idade máxima aceitável dos dados, em horas.",
            ),
        },
    ),
    EspecTool(
        nome_funcao="fn-calculo-simulacao",
        agente="Agente_Risco",
        nome_tool="calcular_simulacao_risco",
        descricao=(
            "Calcula volatilidade, classificação de risco e três cenários "
            "(pessimista ≤ realista ≤ otimista) para um investimento e persiste "
            "a simulação no histórico."
        ),
        propriedades={
            "user_id": ("STRING", "Identificador do usuário."),
            "valor_inicial": ("NUMBER", "Valor inicial do investimento."),
            "prazo_meses": ("INTEGER", "Prazo do investimento em meses."),
            "tipo_ativo": (
                "STRING",
                "Tipo de ativo: RENDA_FIXA, RENDA_VARIAVEL, FII ou CRIPTO.",
            ),
        },
        obrigatorios=("valor_inicial", "prazo_meses", "tipo_ativo"),
    ),
    EspecTool(
        nome_funcao="fn-analise-portfolio",
        agente="Agente_Selecao_Ativos",
        nome_tool="analisar_portfolio",
        descricao=(
            "Analisa a composição e a diversificação do portfólio do usuário e "
            "reporta concentrações acima de 30%."
        ),
        propriedades={
            "user_id": ("STRING", "Identificador do usuário."),
            "tolerancia_risco": (
                "STRING",
                "Tolerância a risco: CONSERVADOR, MODERADO ou ARROJADO.",
            ),
        },
        obrigatorios=("user_id",),
    ),
    EspecTool(
        nome_funcao="fn-selecao-ativos",
        agente="Agente_Selecao_Ativos",
        nome_tool="selecionar_ativos",
        descricao=(
            "Filtra ativos incompatíveis com o perfil e ranqueia os demais por "
            "relação risco/retorno, retornando uma lista ordenada de sugestões. "
            "A recomendação gerada é persistida no histórico do usuário. Com "
            "operacao='consultar_historico', retorna o histórico (recomendações e "
            "simulações) do usuário ordenado por data."
        ),
        propriedades={
            "user_id": ("STRING", "Identificador do usuário."),
            "tolerancia_risco": (
                "STRING",
                "Tolerância a risco: CONSERVADOR, MODERADO ou ARROJADO.",
            ),
            "operacao": (
                "STRING",
                "Opcional: 'consultar_historico' para ler o histórico do usuário.",
            ),
        },
        obrigatorios=("user_id",),
    ),
)


# Mapa de nome de tipo (string do design) para o enum de schema do AgentCore.
_TIPOS_SCHEMA = {
    "STRING": agentcore.SchemaDefinitionType.STRING,
    "NUMBER": agentcore.SchemaDefinitionType.NUMBER,
    "INTEGER": agentcore.SchemaDefinitionType.INTEGER,
    "BOOLEAN": agentcore.SchemaDefinitionType.BOOLEAN,
    "OBJECT": agentcore.SchemaDefinitionType.OBJECT,
    "ARRAY": agentcore.SchemaDefinitionType.ARRAY,
}


class AgentsStack(Stack):
    """Provisiona os recursos do AgentCore suportados via IaC.

    Atributos:
        gateway: Gateway do AgentCore (entrada com JWT do Cognito + tools routing).
        tools: Dicionário ``{nome_tool: GatewayTarget}`` das tools registradas.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        config: ConfigAmbiente,
        user_pool: cognito.IUserPool,
        user_pool_client: cognito.IUserPoolClient,
        funcoes: dict[str, lambda_.IFunction],
        knowledge_base_id: str | None = None,
        knowledge_base_arn: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.config = config
        self._user_pool = user_pool
        self._user_pool_client = user_pool_client
        self._funcoes = funcoes
        self._knowledge_base_id = knowledge_base_id
        self._knowledge_base_arn = knowledge_base_arn

        # Tarefa 9.2 — Gateway (authorizer JWT do Cognito) + tools routing.
        self.gateway = self._criar_gateway()
        self.tools = self._registrar_tools()

        # Tarefa 9.3 — Identity: uma WorkloadIdentity por agente (isolamento).
        self.identidades = self._criar_identidades_agentes()

        # Tarefa 9.5 — Memory: short-term (TTL) + long-term (estratégias), KMS.
        self.memory = self._criar_memory()

        # Tarefa 9.6 — Runtime dos 5 agentes. Repositório ECR (destino da imagem
        # publicada pelo script CLI), role de execução de menor privilégio e o
        # Runtime/RuntimeEndpoint (condicionado à existência da imagem).
        self.repo_ecr = self._criar_repo_ecr()
        self.role_runtime = self._criar_role_runtime()
        self.runtime = self._criar_runtime()
        self.runtime_endpoint = (
            self.runtime.add_endpoint(
                self.config.nome_recurso("prod").replace("-", "_")[:48]
            )
            if self.runtime is not None
            else None
        )

    def _criar_gateway(self) -> agentcore.Gateway:
        """Cria o Gateway do AgentCore com validação de JWT do Cognito.

        - ``authorizer_configuration``: ``GatewayAuthorizer.using_cognito`` valida
          o JWT emitido pelo Cognito (SecurityStack) na entrada, restringindo os
          tokens ao App Client do frontend (``allowed_clients``). Só requisições
          autenticadas alcançam o roteamento de tools (Requisitos 2.1, 12.3).

        Criptografia: usa a chave gerenciada pela AWS (sem CMK). A CMK do projeto
        (SecurityStack) tem key policy restrita a DynamoDB/S3 via ``kms:ViaService``
        e não autoriza o serviço do AgentCore; a CMK protege os dados de fato
        (DynamoDB e S3), enquanto a plataforma do AgentCore usa chave gerenciada
        pela AWS — mesma decisão da Knowledge Base.

        O nome do Gateway recebe o prefixo de ambiente de ``config``.
        """
        return agentcore.Gateway(
            self,
            "GatewayAgentCore",
            gateway_name=self.config.nome_recurso("maia-gateway"),
            description=(
                "Gateway do AgentCore: valida JWT do Cognito na entrada e roteia "
                "as tools (Lambdas) dos agentes do Sistema Multiagente de "
                "Investimentos."
            ),
            authorizer_configuration=agentcore.GatewayAuthorizer.using_cognito(
                user_pool=self._user_pool,
                allowed_clients=[self._user_pool_client],
            ),
        )

    def _schema_entrada(self, spec: EspecTool) -> agentcore.SchemaDefinition:
        """Monta o schema de entrada (objeto) da tool a partir de ``EspecTool``."""
        propriedades = {
            campo: agentcore.SchemaDefinition(
                type=_TIPOS_SCHEMA[tipo],
                description=descricao,
            )
            for campo, (tipo, descricao) in spec.propriedades.items()
        }
        return agentcore.SchemaDefinition(
            type=agentcore.SchemaDefinitionType.OBJECT,
            description=f"Entrada da tool {spec.nome_tool}.",
            properties=propriedades,
            required=list(spec.obrigatorios) or None,
        )

    def _registrar_tools(self) -> dict[str, agentcore.GatewayTarget]:
        """Registra um ``GatewayTarget`` por Lambda de tool (design — matriz).

        Cada target liga a Lambda correspondente ao Gateway com um schema de tool
        inline (``ToolSchema.from_inline``) que descreve nome, descrição e schema
        de entrada — o contrato que o agente usa para invocar a tool. O Gateway
        recebe automaticamente permissão de invocação sobre as Lambdas alvo.
        """
        tools: dict[str, agentcore.GatewayTarget] = {}
        for spec in MATRIZ_TOOLS:
            funcao = self._funcoes[spec.nome_funcao]
            tool_schema = agentcore.ToolSchema.from_inline(
                [
                    agentcore.ToolDefinition(
                        name=spec.nome_tool,
                        description=spec.descricao,
                        input_schema=self._schema_entrada(spec),
                    )
                ]
            )
            target = self.gateway.add_lambda_target(
                f"Target{self._id_target(spec.nome_funcao)}",
                lambda_function=funcao,
                tool_schema=tool_schema,
                gateway_target_name=self.config.nome_recurso(
                    spec.nome_tool.replace("_", "-")
                )[:64],
                description=f"Tool {spec.nome_tool} do {spec.agente}.",
            )
            tools[spec.nome_tool] = target
        return tools

    @staticmethod
    def _id_target(nome_funcao: str) -> str:
        """CamelCase estável para o construct ID do target (ex.: PerfilUsuario)."""
        return "".join(p.capitalize() for p in nome_funcao.replace("fn-", "").split("-"))

    def _criar_identidades_agentes(self) -> dict[str, agentcore.WorkloadIdentity]:
        """Cria uma ``WorkloadIdentity`` por agente (tarefa 9.3 — isolamento).

        Cada agente recebe uma identidade de workload própria no AgentCore. É a
        identidade que o Runtime usa para o agente acionar suas tools pelo
        Gateway; combinada com o authorizer do Gateway e as roles IAM de menor
        privilégio das Lambdas (DataStack, tarefa 4.3), materializa o controle de
        acesso por agente da matriz do design ("Controle de Acesso por Agente" /
        Propriedade 6). O mapa tool-por-agente fica em ``self.tools_por_agente``
        para o wiring do Runtime (tarefa 9.6).

        Retorna ``{nome_agente: WorkloadIdentity}``.
        """
        self.tools_por_agente = _tools_por_agente()
        identidades: dict[str, agentcore.WorkloadIdentity] = {}
        for agente in AGENTES:
            nome = self.config.nome_recurso(f"maia-{agente.lower().replace('_', '-')}")
            identidades[agente] = agentcore.WorkloadIdentity(
                self,
                f"Identity{agente.replace('_', '')}",
                workload_identity_name=nome[:64],
            )
        return identidades

    def _criar_memory(self) -> agentcore.Memory:
        """Cria a AgentCore Memory: short-term (TTL) + long-term (tarefa 9.5).

        - ``expiration_duration``: TTL da short-term memory (contexto da conversa),
          configurável entre 7 e 365 dias — usa ``TTL_SHORT_TERM`` (~90 dias).
        - ``memory_strategies``: memória de longo prazo, sem TTL, com duas
          estratégias gerenciadas escopadas por investidor (``actorId`` = ``userId``):
          preferências persistentes (``USER_PREFERENCE``) e fatos/semântica do
          usuário (``SEMANTIC``). Atende à retenção de preferências e histórico
          entre sessões (design.md — AgentCore Memory; Requisitos 2.3, 10.4).

        Criptografia: chave gerenciada pela AWS (sem CMK), pelo mesmo motivo do
        Gateway — a CMK do projeto é restrita a DynamoDB/S3 e não autoriza o
        serviço do AgentCore.

        Escopo por ``sessionId`` + ``actorId`` (os namespaces usam ``{actorId}``).
        """
        return agentcore.Memory(
            self,
            "MemoryAgentCore",
            memory_name=self.config.nome_recurso("maia_memory").replace("-", "_"),
            description=(
                "Memoria do AgentCore: short-term (contexto de sessao) e long-term "
                "(preferencias e fatos do investidor) do Sistema Multiagente."
            ),
            expiration_duration=TTL_SHORT_TERM,
            memory_strategies=[
                agentcore.ManagedMemoryStrategy(
                    agentcore.MemoryStrategyType.USER_PREFERENCE,
                    strategy_name="preferencias_investidor",
                    namespaces=[_NS_PREFERENCIAS],
                    description="Preferencias persistentes do investidor (long-term).",
                ),
                agentcore.ManagedMemoryStrategy(
                    agentcore.MemoryStrategyType.SEMANTIC,
                    strategy_name="fatos_investidor",
                    namespaces=[_NS_FATOS],
                    description="Fatos/semantica do investidor entre sessoes (long-term).",
                ),
            ],
        )

    def _criar_repo_ecr(self) -> ecr.Repository:
        """Cria o repositório ECR destino da imagem de contêiner dos agentes.

        A imagem (Agente_Orquestrador + 5 agentes especialistas, cada um com
        seu prompt e sua política de tools) é construída e
        publicada por ``scripts/configurar_agentcore.py`` (artefato de build, não
        declarável — ver ``infra/AGENTCORE_IAC.md``). O Runtime referencia esta
        imagem. ``image_scan_on_push`` habilita varredura de vulnerabilidades.
        """
        remocao = self.config.nome == "prod"

        return ecr.Repository(
            self,
            "RepoAgentes",
            repository_name=self.config.nome_recurso("maia-agentes"),
            image_scan_on_push=True,
            empty_on_delete=not remocao,
            removal_policy=RemovalPolicy.RETAIN if remocao else RemovalPolicy.DESTROY,
        )

    def _criar_role_runtime(self) -> iam.Role:
        """Cria a role de execução do Runtime (menor privilégio — tarefa 9.6).

        Assumida por ``bedrock-agentcore.amazonaws.com`` (restrita a esta conta),
        concede apenas o necessário para os agentes operarem:

        - ``bedrock:InvokeModel[WithResponseStream]`` somente no modelo GPT OSS
          120B (todos os 5 agentes usam o mesmo LLM).
        - ``bedrock:Retrieve``/``RetrieveAndGenerate`` no Knowledge Base (RAG
          nativo do Agente_Explicador), quando o ARN da KB é conhecido.
        - Leitura/escrita na AgentCore Memory (short/long-term).
        - Pull da imagem no ECR (o serviço do AgentCore puxa a imagem com esta role).
        - Logs no CloudWatch e rastros do X-Ray (observabilidade — tarefa 11.x).

        As tools são acionadas pelos agentes via Gateway (autenticação por JWT do
        authorizer), não por IAM direto sobre as Lambdas; por isso não há
        ``lambda:InvokeFunction`` aqui — o Gateway é quem invoca as Lambdas (essa
        permissão foi concedida à role do Gateway na tarefa 9.2).
        """
        role = iam.Role(
            self,
            "RoleRuntimeAgentes",
            role_name=self.config.nome_recurso("maia-runtime-role"),
            assumed_by=iam.ServicePrincipal(
                "bedrock-agentcore.amazonaws.com",
                conditions={"StringEquals": {"aws:SourceAccount": self.account}},
            ),
            description=(
                "Role de execucao do Runtime dos agentes (menor privilegio): "
                "modelos Claude Opus/Haiku (inference profile), Knowledge Base, "
                "Memory, ECR e observabilidade."
            ),
        )

        # Invocar os modelos Claude (Opus + Haiku) via inference profile
        # (cross-region): autoriza o profile em us-east-1 e os foundation
        # models subjacentes nas três regiões para onde ele pode rotear.
        recursos_modelos = [
            f"arn:{self.partition}:bedrock:{self.region}:{self.account}:"
            f"inference-profile/{modelo}"
            for modelo in (MODELO_LLM, MODELO_LLM_RAPIDO)
        ] + [
            f"arn:{self.partition}:bedrock:{regiao}::"
            f"foundation-model/{modelo.removeprefix('us.')}"
            for modelo in (MODELO_LLM, MODELO_LLM_RAPIDO)
            for regiao in _REGIOES_INFERENCE_PROFILE
        ]
        role.add_to_policy(
            iam.PolicyStatement(
                sid="InvocarModelosLLM",
                effect=iam.Effect.ALLOW,
                actions=[
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                ],
                resources=recursos_modelos,
            )
        )

        # RAG do Agente_Explicador: recuperar trechos do Knowledge Base.
        if self._knowledge_base_arn is not None:
            role.add_to_policy(
                iam.PolicyStatement(
                    sid="RecuperarKnowledgeBase",
                    effect=iam.Effect.ALLOW,
                    actions=["bedrock:Retrieve", "bedrock:RetrieveAndGenerate"],
                    resources=[self._knowledge_base_arn],
                )
            )

        # Leitura/escrita na Memory (contexto de sessão + preferências/fatos).
        self.memory.grant_read(role)
        self.memory.grant_write(role)

        # Pull da imagem dos agentes no ECR + logs/tracing.
        self.repo_ecr.grant_pull(role)
        role.add_to_policy(
            iam.PolicyStatement(
                sid="ObservabilidadeRuntime",
                effect=iam.Effect.ALLOW,
                actions=[
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                    "xray:PutTraceSegments",
                    "xray:PutTelemetryRecords",
                    "cloudwatch:PutMetricData",
                ],
                resources=["*"],
            )
        )
        return role

    def _criar_runtime(self) -> agentcore.Runtime | None:
        """Cria o Runtime dos 6 agentes, se a imagem de contêiner existir.

        A lógica do Agente_Orquestrador e dos 5 agentes especialistas
        (prompts + política de tools de cada um, incluindo a adaptação de
        linguagem do Agente_Explicador) vive na imagem publicada em
        ``self.repo_ecr`` por ``scripts/configurar_agentcore.py``. A URI da imagem
        é informada pelo contexto do CDK (``-c imagem_agentes=<uri>``).

        Enquanto a imagem não existir (contexto ausente), o Runtime NÃO é criado
        — a plataforma (Gateway, Identity, Memory, ECR, role) é provisionada
        assim mesmo, e o Runtime entra no deploy seguinte, já com a imagem
        publicada, sem alterar os agentes existentes (Requisito 13.1). Retorna
        ``None`` nesse caso.

        Variáveis de ambiente entregam ao contêiner os identificadores de que os
        agentes precisam: endpoint do Gateway (tools), id da Memory, id do
        Knowledge Base (RAG do Explicador) e o id do modelo LLM.
        """
        uri_imagem = self.node.try_get_context(CONTEXTO_IMAGEM_AGENTES)
        if not uri_imagem:
            return None

        ambiente = {
            "GATEWAY_URL": self.gateway.gateway_url,
            "MEMORY_ID": self.memory.memory_id,
            "MODELO_LLM": MODELO_LLM,
        }
        if self._knowledge_base_id is not None:
            ambiente["KNOWLEDGE_BASE_ID"] = self._knowledge_base_id

        return agentcore.Runtime(
            self,
            "RuntimeAgentes",
            runtime_name=self.config.nome_recurso("maia_runtime").replace("-", "_"),
            agent_runtime_artifact=agentcore.AgentRuntimeArtifact.from_image_uri(
                uri_imagem
            ),
            execution_role=self.role_runtime,
            environment_variables=ambiente,
            network_configuration=(
                agentcore.RuntimeNetworkConfiguration.using_public_network()
            ),
            protocol_configuration=agentcore.ProtocolType.HTTP,
            authorizer_configuration=(
                agentcore.RuntimeAuthorizerConfiguration.using_cognito(
                    self._user_pool,
                    [self._user_pool_client],
                )
            ),
            # Repassa o header ``Authorization`` (JWT do Cognito) do chamador para
            # o contêiner. Sem isto, o AgentCore consome o token só na entrada
            # (inbound auth) e NÃO o entrega ao container; os agentes chamariam o
            # Gateway (autorizado por JWT do Cognito) sem token e levariam 401.
            request_header_configuration=agentcore.RequestHeaderConfiguration(
                allowlisted_headers=["Authorization"],
            ),
            tracing_enabled=True,
            description=(
                "Runtime dos 5 agentes (Perfil, Macroeconomico, Risco, "
                "Selecao_Ativos, Explicador) do Sistema Multiagente de "
                "Investimentos, com orquestracao sequencial."
            ),
        )


# ---------------------------------------------------------------------------
# Tarefa 9.3 — Identity (isolamento por agente)
# ---------------------------------------------------------------------------
# Ordem dos 5 agentes (design.md) e as tools que cada um pode acionar. As tools
# são derivadas da MATRIZ_TOOLS; o Agente_Explicador acessa o Knowledge Base
# (RAG nativo do Runtime — ver docstring do módulo), não uma tool do Gateway.
AGENTES: tuple[str, ...] = (
    "Agente_Perfil",
    "Agente_Macroeconomico",
    "Agente_Risco",
    "Agente_Selecao_Ativos",
    "Agente_Explicador",
)

# Configuração da AgentCore Memory (tarefa 9.5).
# Short-term: TTL configurável 7–365 dias (design.md). Padrão ~90 dias.
TTL_SHORT_TERM = Duration.days(90)
# Long-term: namespaces por investidor (actorId = userId), sem TTL.
_NS_PREFERENCIAS = "/investidor/{actorId}/preferencias"
_NS_FATOS = "/investidor/{actorId}/fatos"


def _tools_por_agente() -> dict[str, list[str]]:
    """Agrupa os nomes de tool por agente a partir da MATRIZ_TOOLS."""
    mapa: dict[str, list[str]] = {agente: [] for agente in AGENTES}
    for spec in MATRIZ_TOOLS:
        mapa.setdefault(spec.agente, []).append(spec.nome_tool)
    # O Explicador consome o Knowledge Base (RAG nativo), não uma tool do Gateway.
    mapa["Agente_Explicador"].append("knowledge_base")
    return mapa
