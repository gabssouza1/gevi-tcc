"""Stack de segurança — camada de rede e segurança do backend.

Recursos implementados nesta etapa e nas tarefas seguintes do plano:

- Tarefa 2.2 (implementada) — Chave KMS gerenciada pelo cliente (customer
  managed key) para criptografia em repouso (AES-256) de DynamoDB e S3, com
  rotação automática de material criptográfico, alias descritivo e política de
  chave de menor privilégio. Exposta via ``chave_kms`` para as demais stacks
  (dados, KB) referenciarem.
- Tarefa 2.5 (implementada) — Cognito User Pool (cadastro, login, MFA opcional
  via TOTP, tokens JWT) e App Client público para o frontend; atributos de
  perfil (nível de conhecimento e tolerância a risco) coletados no cadastro.
  Implementado no construct ``cognito_construct.py`` e exposto via ``cognito``.

Tarefa 2.4 (CloudFront + OAC + WAF): implementada na ``FrontendStack``, uma
stack dedicada que recebe o bucket ``s3-investimentos`` da ``DataStack`` por
parâmetro. Colocar o CloudFront aqui, referenciando o bucket da ``DataStack``,
criaria o ciclo ``Security -> Data -> Security``; a stack dedicada mantém a
cadeia de dependências linear (``Security -> Data -> Frontend``).

Tarefa 2.6 (CloudTrail): o trail que registra as chamadas de API é implementado
na ``DataStack``, junto do ``audit-bucket`` (bucket de destino dos logs). Manter
o trail ao lado do bucket evita uma dependência circular entre as stacks, já que
a ``DataStack`` depende desta stack apenas pela chave KMS. A ``SecurityStack``
contribui com a chave (tarefa 2.2), cuja key policy é estendida na ``DataStack``
para autorizar o serviço ``cloudtrail.amazonaws.com``.

Requisitos cobertos pela camada: 1.1, 1.4, 2.1, 12.1, 12.3.
"""

from __future__ import annotations

from aws_cdk import RemovalPolicy, Stack
from aws_cdk import aws_iam as iam
from aws_cdk import aws_kms as kms
from constructs import Construct

from config import ConfigAmbiente
from stacks.cognito_construct import CognitoConstruct


class SecurityStack(Stack):
    """Provisiona KMS e Cognito.

    Nesta etapa provisiona a chave KMS gerenciada pelo cliente (tarefa 2.2) e o
    Cognito (tarefa 2.5). O CloudFront/OAC/WAF (tarefa 2.4) fica na
    ``FrontendStack`` e o CloudTrail (tarefa 2.6) na ``DataStack`` (junto do
    ``audit-bucket``), ambos para evitar dependências circulares entre stacks.

    Atributos:
        chave_kms: Chave KMS gerenciada pelo cliente para criptografia em
            repouso de DynamoDB e S3. Consumida pelas stacks de dados e KB.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        config: ConfigAmbiente,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.config = config

        # Tarefa 2.2 — Chave KMS gerenciada pelo cliente.
        self.chave_kms = self._criar_chave_kms()

        # Tarefa 2.5 — Cognito (User Pool + App Client). Implementado em um
        # construct separado (``cognito_construct.py``) para reduzir conflito de
        # edição nesta stack. Expõe ``user_pool`` e ``user_pool_client`` para
        # wiring posterior (ex.: validação de JWT no AgentCore Gateway).
        self.cognito = CognitoConstruct(self, "Cognito", config=config)
        self.user_pool = self.cognito.user_pool
        self.user_pool_client = self.cognito.user_pool_client

        # Tarefa 2.4 (CloudFront + OAC + WAF): implementada na FrontendStack.
        # Tarefa 2.6 (CloudTrail): implementada na DataStack, junto do
        # audit-bucket, para evitar dependência circular entre as stacks.

    def _criar_chave_kms(self) -> kms.Key:
        """Cria a chave KMS gerenciada pelo cliente para dados em repouso.

        A chave é usada para criptografia AES-256 em repouso de DynamoDB e S3
        (design.md — camada "Dados em repouso"). Características:

        - ``enable_key_rotation``: rotação automática anual do material
          criptográfico (boa prática de segurança / requisito 12.1).
        - ``key_spec`` simétrico (``SYMMETRIC_DEFAULT``): compatível com a
          criptografia de envelope usada por DynamoDB e S3.
        - Alias descritivo (``alias/maia-<ambiente>-dados``) para identificação.
        - ``RemovalPolicy.RETAIN``: evita destruição acidental de uma chave que
          protege dados persistentes; a exclusão exige ação explícita.
        - Política de chave de menor privilégio (ver ``_politica_chave``).

        O prefixo por ambiente vem de ``config`` para evitar colisão de alias
        entre ambientes na mesma conta.
        """
        alias = self.config.nome_recurso("maia-dados")

        return kms.Key(
            self,
            "ChaveDadosRepouso",
            alias=f"alias/{alias}",
            description=(
                "Chave gerenciada pelo cliente para criptografia em repouso "
                "(AES-256) de DynamoDB e S3 do Sistema Multiagente de "
                f"Investimentos (ambiente {self.config.nome})."
            ),
            enable_key_rotation=True,
            key_spec=kms.KeySpec.SYMMETRIC_DEFAULT,
            key_usage=kms.KeyUsage.ENCRYPT_DECRYPT,
            removal_policy=RemovalPolicy.RETAIN,
            policy=self._politica_chave(),
        )

    def _politica_chave(self) -> iam.PolicyDocument:
        """Monta a política de chave (key policy) de menor privilégio.

        Princípios aplicados:

        - Administração da chave restrita ao root da conta (que delega via IAM),
          sem conceder ``kms:*`` a nenhum principal externo.
        - Uso da chave (encrypt/decrypt/geração de data keys) concedido a
          serviços AWS somente por meio da condição ``kms:ViaService``, de modo
          que apenas DynamoDB e S3 na região do stack possam usar a chave, e
          apenas em nome de principais desta conta (``kms:CallerAccount``).

        Isso segue o menor privilégio: não há ``Allow`` amplo de uso direto; o
        acesso das stacks de dados/S3 é concedido depois via ``grant_*`` (que
        adiciona permissões IAM aos papéis consumidores), enquanto a key policy
        restringe a superfície ao par de serviços esperado.
        """
        conta = self.account
        regiao = self.region

        # Permite que a conta administre a chave via políticas IAM (padrão
        # recomendado pela AWS: delega o controle de acesso ao IAM da conta).
        admin_via_iam = iam.PolicyStatement(
            sid="PermitirAdministracaoViaIAMDaConta",
            effect=iam.Effect.ALLOW,
            principals=[iam.AccountRootPrincipal()],
            actions=["kms:*"],
            resources=["*"],
        )

        # Permite o uso da chave apenas por DynamoDB e S3 (via serviço), em nome
        # de principais desta conta. Restringe a superfície de uso da chave.
        uso_por_servicos = iam.PolicyStatement(
            sid="PermitirUsoPorDynamoDBeS3",
            effect=iam.Effect.ALLOW,
            principals=[iam.AnyPrincipal()],
            actions=[
                "kms:Encrypt",
                "kms:Decrypt",
                "kms:ReEncrypt*",
                "kms:GenerateDataKey*",
                "kms:DescribeKey",
                "kms:CreateGrant",
            ],
            resources=["*"],
            conditions={
                "StringEquals": {
                    "kms:CallerAccount": conta,
                    "kms:ViaService": [
                        f"dynamodb.{regiao}.amazonaws.com",
                        f"s3.{regiao}.amazonaws.com",
                    ],
                },
            },
        )

        return iam.PolicyDocument(statements=[admin_via_iam, uso_por_servicos])
