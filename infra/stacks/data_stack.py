"""Stack de dados — armazenamento, agendamento e permissões.

Recursos implementados nesta etapa e nas tarefas seguintes do plano:

- Tarefa 2.3 (implementada) — Buckets S3: ``s3-investimentos`` (prefixos
  lógicos ``frontend/``, ``datasets/`` e ``kb-documentos/``) e ``audit-bucket``
  (logs do CloudTrail). Ambos privados (sem acesso público), versionados,
  criptografados em repouso com a chave KMS gerenciada pelo cliente
  (``SecurityStack.chave_kms``) e com HTTPS obrigatório (enforce SSL).
- Tarefa 2.6 (implementada) — CloudTrail (``maia-<ambiente>-trail``) registrando
  as chamadas de API da conta e eventos de dados de S3 relevantes, entregando os
  logs ao ``audit-bucket`` (prefixo ``cloudtrail-logs/``). Implementado aqui, e
  não na ``SecurityStack``, porque o bucket de destino vive nesta stack: manter
  o trail junto do bucket evita uma dependência circular entre as stacks (a
  ``DataStack`` já depende da ``SecurityStack`` pela chave KMS) e concentra os
  ajustes de política de bucket e de chave necessários para a entrega dos logs.
- Tarefa 4.1 (implementada) — Tabelas DynamoDB: ``Users``,
  ``EconomicIndicators``, ``Portfolios`` e ``Historico``. Billing on-demand
  (``PAY_PER_REQUEST``), criptografia em repouso com a chave KMS gerenciada pelo
  cliente (``self.chave_kms``), point-in-time recovery habilitado e política de
  remoção ``RETAIN`` em prod / ``DESTROY`` em não-prod. Expostas como atributos
  (``tabela_users``, ``tabela_indicadores``, ``tabela_portfolios``,
  ``tabela_historico``) para as Lambdas (tarefas 5/6) e roles IAM (tarefa 4.3).
- Tarefa 4.2 (regra EventBridge do pipeline de indicadores): a regra
  ``regra-indicadores`` (``rate(5 minutes)`` acionando ``fn-consulta-APIs``) é
  criada na ``ComputeStack``, co-localizada com a função-alvo. Colocá-la aqui e
  apontá-la para a Lambda da ``ComputeStack`` criaria um ciclo Data<->Compute
  (a ``ComputeStack`` já depende desta stack pelas tabelas/roles); por isso a
  regra e seu alvo ficam juntos na ``ComputeStack`` (wiring da tarefa 12.1).
- Tarefa 4.3 (implementada) — Roles IAM de menor privilégio, uma por Lambda
  (Requisito 12.2). Cada role recebe a ``AWSLambdaBasicExecutionRole`` (logs no
  CloudWatch) mais as permissões mínimas às tabelas/bucket que a função acessa,
  concedidas pelos métodos ``grant_*`` dos constructs (que já anexam o uso da
  chave KMS às roles que tocam recursos criptografados), conforme a matriz do
  design.md ("Funções Lambda" e "Controle de Acesso por Agente"). As roles são
  expostas no dicionário ``self.roles_lambdas`` (por nome de função) para a
  ``ComputeStack`` (tarefas 5.x/6.x) atribuir a cada Lambda.

Requisitos cobertos pela camada: 1.1, 2.5, 2.6, 3.3, 7.1, 7.2, 8.1, 8.3, 8.4,
9.1, 12.1, 12.2, 12.4, 13.3.
"""

from __future__ import annotations

from aws_cdk import RemovalPolicy, Stack
from aws_cdk import aws_cloudtrail as cloudtrail
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_iam as iam
from aws_cdk import aws_kms as kms
from aws_cdk import aws_s3 as s3
from constructs import Construct

from config import ConfigAmbiente


class DataStack(Stack):
    """Provisiona S3, DynamoDB, EventBridge e roles IAM.

    Nesta etapa provisiona os dois buckets S3 (tarefa 2.3), o CloudTrail
    (tarefa 2.6) e as 4 tabelas DynamoDB (tarefa 4.1). Os demais construtos são
    criados nas tarefas 4.2 (EventBridge) e 4.3 (roles IAM).

    Atributos:
        chave_kms: Chave KMS gerenciada pelo cliente (vinda da ``SecurityStack``)
            usada para criptografia em repouso dos buckets e, adiante, das
            tabelas DynamoDB.
        bucket_investimentos: Bucket ``s3-investimentos`` (frontend/, datasets/,
            kb-documentos/). Consumido pelo CloudFront/OAC (tarefa 2.4), pelas
            Lambdas (``fn-analise-portfolio``) e pela Knowledge Base (tarefa 8.x).
        bucket_auditoria: Bucket ``audit-bucket`` para os logs do CloudTrail
            (tarefa 2.6).
        trail: Trail do CloudTrail (tarefa 2.6) que entrega os logs de auditoria
            ao ``bucket_auditoria``.
        tabela_users: Tabela DynamoDB ``Users`` (PK ``userId``) — dados
            cadastrais, perfil de risco, preferências e notificações in-app.
            Consumida por ``fn-perfil-usuario`` e ``fn-selecao-ativos``.
        tabela_indicadores: Tabela ``EconomicIndicators`` (PK ``indicatorId``,
            SK ``date``) — séries de Selic, IPCA, dólar e CDI alimentadas pelo
            pipeline do EventBridge (``fn-consulta-APIs``) e lidas pelas demais.
        tabela_portfolios: Tabela ``Portfolios`` (PK ``userId``, SK ``assetId``)
            — composição do portfólio do usuário, lida por ``fn-analise-portfolio``.
        tabela_historico: Tabela ``Historico`` (PK ``userId``, SK ``timestamp``)
            — recomendações e simulações geradas para o usuário, de forma
            consultável e rastreável.
        roles_lambdas: Dicionário ``{nome_da_funcao: iam.Role}`` com uma role de
            execução de menor privilégio por Lambda (tarefa 4.3). Cada role tem
            logs no CloudWatch (``AWSLambdaBasicExecutionRole``) e apenas as
            permissões de dados que a função usa (Requisito 12.2). Consumido pela
            ``ComputeStack`` (tarefas 5.x/6.x) ao criar cada função com
            ``role=self.roles_lambdas["fn-..."]``.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        config: ConfigAmbiente,
        chave_kms: kms.IKey,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.config = config
        self.chave_kms = chave_kms

        # Tarefa 2.3 — Buckets S3 privados, versionados e criptografados por KMS.
        self.bucket_investimentos = self._criar_bucket_investimentos()
        self.bucket_auditoria = self._criar_bucket_auditoria()

        # Tarefa 2.6 — CloudTrail entregando logs de auditoria ao audit-bucket.
        self.trail = self._criar_cloudtrail()

        # Tarefa 4.1 — Tabelas DynamoDB (billing on-demand + criptografia KMS).
        (
            self.tabela_users,
            self.tabela_indicadores,
            self.tabela_portfolios,
            self.tabela_historico,
        ) = self._criar_tabelas_dynamodb()

        # Tarefa 4.3 — Roles IAM de menor privilégio (uma por Lambda).
        self.roles_lambdas = self._criar_roles_lambdas()

        # Tarefa 2.4 (suporte) — Autoriza o CloudFront (OAC) a ler o prefixo
        # frontend/ e a usar a chave KMS, com curinga de distribuição, para que a
        # FrontendStack não precise mutar estas políticas (evita o ciclo entre
        # stacks). Ver frontend_stack (docstring do módulo).
        self._autorizar_cloudfront_frontend()

    def _nome_bucket(self, base: str) -> str:
        """Monta o nome físico do bucket a partir do nome base de ``config``.

        Nomes de bucket S3 são globais e precisam ser únicos entre todas as
        contas AWS. Por isso o nome base do ``config`` (ex.: ``s3-investimentos``)
        recebe o prefixo de ambiente e um sufixo com conta e região, evitando
        colisão de nomes ao fazer deploy (design.md — buckets ``s3-investimentos``
        e ``audit-bucket``).
        """
        base_prefixado = self.config.nome_recurso(base)
        return f"{base_prefixado}-{self.account}-{self.region}".lower()

    def _politica_remocao(self) -> tuple[RemovalPolicy, bool]:
        """Define a política de remoção e o auto-delete conforme o ambiente.

        Em ``prod`` os buckets são retidos (``RETAIN``) para evitar perda
        acidental de dados persistentes e de logs de auditoria; não há
        auto-delete de objetos. Em ambientes não-prod os buckets são destruídos
        junto com a stack e seus objetos são removidos automaticamente, o que
        agiliza a iteração de desenvolvimento.
        """
        if self.config.nome == "prod":
            return RemovalPolicy.RETAIN, False
        return RemovalPolicy.DESTROY, True

    def _criar_bucket_investimentos(self) -> s3.Bucket:
        """Cria o bucket ``s3-investimentos`` (frontend, datasets, KB).

        Estrutura de prefixos lógicos (design.md): ``frontend/`` (assets do
        frontend servidos pelo CloudFront via OAC), ``datasets/`` (séries
        históricas lidas por ``fn-analise-portfolio``) e ``kb-documentos/``
        (documentos indexados pela Knowledge Base). Prefixos em S3 são lógicos:
        são materializados quando os objetos correspondentes são enviados
        (deploy do frontend, ingestão do KB), não exigindo criação declarativa.

        Configuração de segurança:
        - ``block_public_access = BLOCK_ALL``: bucket privado; o acesso ao
          frontend é feito apenas via CloudFront/OAC (tarefa 2.4).
        - ``encryption = KMS`` com a chave gerenciada pelo cliente: criptografia
          em repouso AES-256 (Requisito 12.1). ``bucket_key_enabled`` reduz o
          custo de chamadas ao KMS via S3 Bucket Keys.
        - ``enforce_ssl``: nega requisições que não usem HTTPS (dados em trânsito
          — Requisito 12.1).
        - ``versioned``: versionamento habilitado para proteção contra
          sobrescrita/exclusão acidental.
        """
        remocao, auto_delete = self._politica_remocao()

        return s3.Bucket(
            self,
            "BucketInvestimentos",
            bucket_name=self._nome_bucket(self.config.nomes.bucket_investimentos),
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.KMS,
            encryption_key=self.chave_kms,
            bucket_key_enabled=True,
            enforce_ssl=True,
            versioned=True,
            removal_policy=remocao,
            auto_delete_objects=auto_delete,
        )

    def _criar_bucket_auditoria(self) -> s3.Bucket:
        """Cria o ``audit-bucket`` para os logs do CloudTrail (tarefa 2.6).

        Mesmas garantias de segurança do bucket de investimentos (privado,
        versionado, criptografado por KMS e HTTPS obrigatório — Requisitos 12.1
        e 12.4). Os logs do CloudTrail ficam sob o prefixo lógico
        ``cloudtrail-logs/`` (design.md).

        A entrega de logs do CloudTrail para um bucket criptografado por chave
        gerenciada pelo cliente exige (a) uma política de bucket autorizando o
        serviço ``cloudtrail.amazonaws.com`` e (b) permissão de uso da chave KMS
        pelo CloudTrail. Ambas são configuradas em ``_criar_cloudtrail`` (tarefa
        2.6): a política de bucket é adicionada automaticamente pelo construct
        ``Trail`` e a autorização da chave é concedida em
        ``_autorizar_cloudtrail_na_chave``.
        """
        remocao, auto_delete = self._politica_remocao()

        return s3.Bucket(
            self,
            "BucketAuditoria",
            bucket_name=self._nome_bucket(self.config.nomes.bucket_auditoria),
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.KMS,
            encryption_key=self.chave_kms,
            bucket_key_enabled=True,
            enforce_ssl=True,
            versioned=True,
            removal_policy=remocao,
            auto_delete_objects=auto_delete,
        )

    def _autorizar_cloudtrail_na_chave(self) -> None:
        """Autoriza o serviço CloudTrail a usar a chave KMS do ``audit-bucket``.

        O ``audit-bucket`` tem criptografia padrão por chave gerenciada pelo
        cliente (CMK). Para entregar os logs, o CloudTrail precisa gerar chaves
        de dados (``kms:GenerateDataKey*``) e inspecionar a chave
        (``kms:DescribeKey``). Como a key policy criada na ``SecurityStack``
        (tarefa 2.2) só libera o uso via serviço para DynamoDB e S3, adicionamos
        aqui as instruções específicas para ``cloudtrail.amazonaws.com``.

        Restrições de menor privilégio aplicadas:

        - ``kms:GenerateDataKey*`` é liberado apenas quando o contexto de
          criptografia identifica um trail desta conta
          (``kms:EncryptionContext:aws:cloudtrail:arn``), evitando que o
          principal de serviço use a chave fora do fluxo do CloudTrail.
        - As instruções são adicionadas à própria resource policy da chave (que
          vive na ``SecurityStack``); nenhum atributo do trail é referenciado por
          token, apenas um padrão de ARN construído a partir de conta/região.
          Isso evita uma referência reversa ``SecurityStack -> DataStack`` que
          criaria dependência circular entre as stacks.
        """
        arn_trails_conta = (
            f"arn:{self.partition}:cloudtrail:*:{self.account}:trail/*"
        )
        principal_cloudtrail = iam.ServicePrincipal("cloudtrail.amazonaws.com")

        self.chave_kms.add_to_resource_policy(
            iam.PolicyStatement(
                sid="PermitirCloudTrailGerarChaveDeDados",
                effect=iam.Effect.ALLOW,
                principals=[principal_cloudtrail],
                actions=["kms:GenerateDataKey*"],
                resources=["*"],
                conditions={
                    "StringLike": {
                        "kms:EncryptionContext:aws:cloudtrail:arn": arn_trails_conta,
                    },
                },
            )
        )
        self.chave_kms.add_to_resource_policy(
            iam.PolicyStatement(
                sid="PermitirCloudTrailDescreverChave",
                effect=iam.Effect.ALLOW,
                principals=[principal_cloudtrail],
                actions=["kms:DescribeKey"],
                resources=["*"],
            )
        )

    def _criar_cloudtrail(self) -> cloudtrail.Trail:
        """Cria o trail do CloudTrail para auditoria (Requisito 12.4).

        Registra as chamadas de API da conta (eventos de gerenciamento) e as
        entrega ao ``audit-bucket`` sob o prefixo ``cloudtrail-logs/`` (design.md
        — camada de compliance e auditoria). Características:

        - ``bucket``/``s3_key_prefix``: destino dos logs no ``audit-bucket``. O
          construct ``Trail`` adiciona automaticamente à política do bucket as
          permissões de entrega do CloudTrail (``s3:GetBucketAcl`` e
          ``s3:PutObject`` com ACL ``bucket-owner-full-control``).
        - ``encryption_key``: usa a mesma CMK do bucket para criptografar os
          arquivos de log (SSE-KMS). A autorização de uso da chave é concedida em
          ``_autorizar_cloudtrail_na_chave``.
        - ``management_events = ReadWriteType.ALL`` e
          ``include_global_service_events``: capturam todas as chamadas de
          gerenciamento, inclusive de serviços globais (ex.: IAM), atendendo à
          auditoria "quem acessou o quê, quando e de onde".
        - ``is_multi_region_trail``: registra eventos de todas as regiões, para
          não deixar lacunas de auditoria.
        - ``enable_file_validation``: gera arquivos de integridade (digest) para
          detectar adulteração dos logs — relevante para compliance, sem custo
          adicional de armazenamento significativo.

        Eventos de dados (Requisito 12.4 — "eventos de dados relevantes"): são
        habilitados apenas para o ``bucket_investimentos`` (acesso a datasets e
        documentos do KB), via ``add_s3_event_selector``. O ``audit-bucket`` é
        deliberadamente excluído para não registrar as próprias escritas do
        CloudTrail, o que geraria um laço de logs e custo desnecessário. Os
        eventos de dados do DynamoDB serão adicionados quando as tabelas forem
        criadas (tarefa 4.1), evitando acoplamento nesta etapa.
        """
        self._autorizar_cloudtrail_na_chave()

        trail = cloudtrail.Trail(
            self,
            "TrailAuditoria",
            trail_name=self.config.nome_recurso("maia-trail"),
            bucket=self.bucket_auditoria,
            s3_key_prefix="cloudtrail-logs",
            encryption_key=self.chave_kms,
            management_events=cloudtrail.ReadWriteType.ALL,
            include_global_service_events=True,
            is_multi_region_trail=True,
            enable_file_validation=True,
        )

        # Eventos de dados de S3 do bucket de investimentos (leitura e escrita).
        # Escopo restrito a um bucket específico mantém o custo sob controle.
        trail.add_s3_event_selector(
            [cloudtrail.S3EventSelector(bucket=self.bucket_investimentos)],
            read_write_type=cloudtrail.ReadWriteType.ALL,
            include_management_events=False,
        )

        return trail

    def _criar_tabela(
        self,
        construct_id: str,
        *,
        nome_base: str,
        partition_key: dynamodb.Attribute,
        sort_key: dynamodb.Attribute | None = None,
    ) -> dynamodb.Table:
        """Cria uma tabela DynamoDB com o padrão de segurança da camada de dados.

        Configuração aplicada a todas as 4 tabelas (design.md — Tabelas
        DynamoDB):

        - ``billing_mode = PAY_PER_REQUEST``: cobrança on-demand, sem
          provisionamento de capacidade. Escala automaticamente conforme a carga
          (design.md — Escalabilidade horizontal) e evita throttling nos picos do
          pipeline de indicadores e das consultas dos agentes.
        - ``encryption = CUSTOMER_MANAGED`` com ``self.chave_kms``: criptografia
          em repouso pela chave gerenciada pelo cliente (CMK) provisionada na
          ``SecurityStack`` (Requisito 12.1). A key policy dessa chave já libera
          o uso via serviço para DynamoDB.
        - ``point_in_time_recovery = True``: recuperação em qualquer ponto dos
          últimos 35 dias, protegendo os dados transacionais e o histórico
          rastreável contra escrita/exclusão acidental (Requisitos 2.5, 2.6).
        - ``removal_policy``: ``RETAIN`` em ``prod`` (preserva os dados ao
          destruir a stack) e ``DESTROY`` em ambientes não-prod (agiliza a
          iteração de desenvolvimento), reaproveitando ``_politica_remocao``.

        O nome físico recebe o prefixo de ambiente de ``config`` (ex.:
        ``dev-Users``), mantendo as chaves exatamente como no design.
        """
        remocao, _ = self._politica_remocao()

        return dynamodb.Table(
            self,
            construct_id,
            table_name=self.config.nome_recurso(nome_base),
            partition_key=partition_key,
            sort_key=sort_key,
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            encryption=dynamodb.TableEncryption.CUSTOMER_MANAGED,
            encryption_key=self.chave_kms,
            point_in_time_recovery=True,
            removal_policy=remocao,
        )

    def _criar_tabelas_dynamodb(
        self,
    ) -> tuple[dynamodb.Table, dynamodb.Table, dynamodb.Table, dynamodb.Table]:
        """Cria as 4 tabelas DynamoDB do backend (tarefa 4.1).

        As chaves seguem exatamente o design.md (seção "Tabelas DynamoDB"):

        - ``Users``: PK ``userId``. Dados cadastrais, perfil de risco, nível de
          conhecimento, preferências, limiares de alerta e notificações in-app.
        - ``EconomicIndicators``: PK ``indicatorId`` + SK ``date``. Valores
          históricos de Selic, IPCA, dólar e CDI, alimentados pelo pipeline do
          EventBridge (``fn-consulta-APIs``).
        - ``Portfolios``: PK ``userId`` + SK ``assetId``. Composição do portfólio
          (ativos, percentuais, valores e tipo de ativo dentre os ``Tipos_Ativo``).
        - ``Historico``: PK ``userId`` + SK ``timestamp``. Recomendações e
          simulações geradas para o usuário. A combinação ``userId`` (partição) +
          ``timestamp`` (ordenação) torna o histórico consultável por usuário e
          ordenável no tempo, atendendo à rastreabilidade exigida (Requisitos
          2.5, 2.6): o Agente_Selecao_Ativos/Explicador grava a recomendação e
          ``fn-calculo-simulacao`` grava a simulação, ambos associados ao
          ``userId`` com ``timestamp``.
        """
        tabela_users = self._criar_tabela(
            "TabelaUsers",
            nome_base=self.config.nomes.tabela_users,
            partition_key=dynamodb.Attribute(
                name="userId", type=dynamodb.AttributeType.STRING
            ),
        )
        tabela_indicadores = self._criar_tabela(
            "TabelaEconomicIndicators",
            nome_base=self.config.nomes.tabela_indicadores,
            partition_key=dynamodb.Attribute(
                name="indicatorId", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="date", type=dynamodb.AttributeType.STRING
            ),
        )
        tabela_portfolios = self._criar_tabela(
            "TabelaPortfolios",
            nome_base=self.config.nomes.tabela_portfolios,
            partition_key=dynamodb.Attribute(
                name="userId", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="assetId", type=dynamodb.AttributeType.STRING
            ),
        )
        tabela_historico = self._criar_tabela(
            "TabelaHistorico",
            nome_base=self.config.nomes.tabela_historico,
            partition_key=dynamodb.Attribute(
                name="userId", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="timestamp", type=dynamodb.AttributeType.STRING
            ),
        )

        return (
            tabela_users,
            tabela_indicadores,
            tabela_portfolios,
            tabela_historico,
        )

    def _autorizar_cloudfront_frontend(self) -> None:
        """Autoriza o CloudFront (OAC) a servir o ``frontend/`` do bucket.

        A ``FrontendStack`` importa o bucket por nome e, por isso, não pode mutar
        as políticas do bucket nem da chave KMS. Estas autorizações são
        concedidas aqui, no lado que é dono dos recursos, usando um **curinga**
        de ID de distribuição na condição ``AWS:SourceArn``
        (``arn:<partition>:cloudfront::<conta>:distribution/*``). Como a condição
        é uma string estática (sem token da distribuição), não há referência
        reversa ``DataStack -> FrontendStack`` e o ciclo entre stacks é evitado —
        o mesmo princípio de ``_autorizar_cloudtrail_na_chave``.

        Duas concessões de menor privilégio, restritas ao serviço do CloudFront e
        a distribuições desta conta:

        - Bucket: ``s3:GetObject`` apenas nos objetos sob ``frontend/`` (os assets
          servidos pela distribuição), mantendo o bucket privado para o restante.
        - Chave KMS: ``kms:Decrypt`` para o CloudFront descriptografar os objetos
          do ``frontend/`` criptografados com a CMK (SSE-KMS).
        """
        arn_distribuicoes_conta = (
            f"arn:{self.partition}:cloudfront::{self.account}:distribution/*"
        )
        principal_cloudfront = iam.ServicePrincipal("cloudfront.amazonaws.com")
        condicao_origem = {
            "StringLike": {"AWS:SourceArn": arn_distribuicoes_conta}
        }

        # Leitura dos assets do frontend pelo CloudFront (OAC).
        self.bucket_investimentos.add_to_resource_policy(
            iam.PolicyStatement(
                sid="PermitirLeituraCloudFrontOAC",
                effect=iam.Effect.ALLOW,
                principals=[principal_cloudfront],
                actions=["s3:GetObject"],
                resources=[self.bucket_investimentos.arn_for_objects("frontend/*")],
                conditions=condicao_origem,
            )
        )

        # Uso da chave KMS pelo CloudFront para descriptografar os assets.
        self.chave_kms.add_to_resource_policy(
            iam.PolicyStatement(
                sid="PermitirCloudFrontDecryptFrontend",
                effect=iam.Effect.ALLOW,
                principals=[principal_cloudfront],
                actions=["kms:Decrypt"],
                resources=["*"],
                conditions=condicao_origem,
            )
        )

    def _criar_role_lambda(self, nome_funcao: str, construct_id: str) -> iam.Role:
        """Cria uma role de execução base para uma Lambda (tarefa 4.3).

        A role assume o principal ``lambda.amazonaws.com`` e recebe apenas a
        política gerenciada ``AWSLambdaBasicExecutionRole``, que concede o
        mínimo necessário para publicar logs no CloudWatch Logs (criar grupo,
        stream e ``PutLogEvents``) — Requisito 14.4. As permissões de acesso a
        dados (DynamoDB/S3) são adicionadas depois, por função, via os métodos
        ``grant_*`` dos constructs, de modo que cada role receba exatamente as
        ações e recursos de que a função precisa (menor privilégio — Requisito
        12.2).

        O nome físico recebe o prefixo de ambiente de ``config`` (ex.:
        ``dev-fn-perfil-usuario-role``), mantendo a correspondência 1:1 entre
        função Lambda e role explícita na conta.
        """
        return iam.Role(
            self,
            construct_id,
            role_name=self.config.nome_recurso(f"{nome_funcao}-role"),
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            description=(
                f"Role de execucao de menor privilegio da Lambda {nome_funcao} "
                "(tarefa 4.3)."
            ),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                )
            ],
        )

    def _criar_roles_lambdas(self) -> dict[str, iam.Role]:
        """Cria as 6 roles IAM de menor privilégio, uma por Lambda (tarefa 4.3).

        Cada role recebe apenas as permissões estritamente necessárias às
        tabelas/buckets que a função acessa, conforme a matriz do design.md
        (seções "Funções Lambda" e "Controle de Acesso por Agente") — Requisito
        12.2. As permissões são concedidas pelos métodos ``grant_*`` dos
        constructs de DynamoDB e S3, que geram políticas de menor privilégio
        (ações mínimas + ARNs específicos, incluindo os índices das tabelas).

        Mapeamento role → permissões:

        - ``fn-perfil-usuario`` → leitura e escrita em ``Users`` (CRUD do
          Perfil_Investidor).
        - ``fn-consulta-APIs`` → escrita em ``EconomicIndicators`` (pipeline
          assíncrono persiste os indicadores) e leitura/escrita em ``Users``
          (verifica os limiares de alerta configurados pelo usuário e grava a
          notificação in-app quando excedido — design.md, "Pipeline Assíncrono
          de Indicadores"). O egress HTTPS às APIs externas (BCB, B3) é uma
          questão de rede, não de IAM, e por isso não gera política aqui.
        - ``fn-consulta-indicadores`` → apenas leitura em ``EconomicIndicators``
          (Propriedade 10 — a função só lê).
        - ``fn-calculo-simulacao`` → leitura em ``EconomicIndicators`` e escrita
          em ``Historico`` (persiste a simulação — tarefa 6.8).
        - ``fn-analise-portfolio`` → leitura em ``Portfolios`` e leitura no
          bucket ``s3-investimentos`` (datasets/documentos de ativos).
        - ``fn-selecao-ativos`` → leitura em ``EconomicIndicators`` e ``Users`` e
          leitura/escrita em ``Historico`` (persiste a recomendação — tarefa 9.9
          — e consulta o histórico — tarefa 9.10).

        Uso da chave KMS (``self.chave_kms``): não é concedido explicitamente.
        As tabelas e o bucket são criptografados por essa CMK e os próprios
        métodos ``grant_read_data``/``grant_write_data``/``grant_read_write_data``
        (DynamoDB) e ``grant_read`` (S3) já anexam à role as permissões de uso da
        chave (``kms:Decrypt`` para leitura; ``kms:Decrypt`` +
        ``kms:GenerateDataKey*`` para escrita). Assim, somente as roles que de
        fato acessam recursos criptografados recebem acesso à chave, preservando
        o menor privilégio.

        Retorna um dicionário ``{nome_da_funcao: role}`` (exposto como
        ``self.roles_lambdas``) para a ``ComputeStack`` (tarefas 5.x/6.x)
        atribuir a role correspondente a cada Lambda no parâmetro ``role``.
        """
        # Uma role base por função. Os construct IDs são estáveis para não
        # recriar recursos entre deploys.
        roles = {
            "fn-perfil-usuario": self._criar_role_lambda(
                "fn-perfil-usuario", "RolePerfilUsuario"
            ),
            "fn-consulta-APIs": self._criar_role_lambda(
                "fn-consulta-APIs", "RoleConsultaApis"
            ),
            "fn-consulta-indicadores": self._criar_role_lambda(
                "fn-consulta-indicadores", "RoleConsultaIndicadores"
            ),
            "fn-calculo-simulacao": self._criar_role_lambda(
                "fn-calculo-simulacao", "RoleCalculoSimulacao"
            ),
            "fn-analise-portfolio": self._criar_role_lambda(
                "fn-analise-portfolio", "RoleAnalisePortfolio"
            ),
            "fn-selecao-ativos": self._criar_role_lambda(
                "fn-selecao-ativos", "RoleSelecaoAtivos"
            ),
        }

        # fn-perfil-usuario: CRUD do perfil do investidor em Users.
        self.tabela_users.grant_read_write_data(roles["fn-perfil-usuario"])

        # fn-consulta-APIs: escreve indicadores e lê/escreve notificações in-app
        # (limiares de alerta) em Users.
        self.tabela_indicadores.grant_write_data(roles["fn-consulta-APIs"])
        self.tabela_users.grant_read_write_data(roles["fn-consulta-APIs"])

        # fn-consulta-indicadores: apenas leitura dos indicadores persistidos.
        self.tabela_indicadores.grant_read_data(roles["fn-consulta-indicadores"])

        # fn-calculo-simulacao: lê indicadores e persiste a simulação no Historico.
        self.tabela_indicadores.grant_read_data(roles["fn-calculo-simulacao"])
        self.tabela_historico.grant_write_data(roles["fn-calculo-simulacao"])

        # fn-analise-portfolio: lê o portfólio e os datasets/documentos no S3.
        self.tabela_portfolios.grant_read_data(roles["fn-analise-portfolio"])
        self.bucket_investimentos.grant_read(roles["fn-analise-portfolio"])

        # fn-selecao-ativos: lê indicadores e perfil, persiste a recomendação
        # (tarefa 9.9) e consulta o histórico (tarefa 9.10) — leitura e escrita
        # no Historico.
        self.tabela_indicadores.grant_read_data(roles["fn-selecao-ativos"])
        self.tabela_users.grant_read_data(roles["fn-selecao-ativos"])
        self.tabela_historico.grant_read_write_data(roles["fn-selecao-ativos"])

        return roles
