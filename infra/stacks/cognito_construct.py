"""Construct de autenticação — Amazon Cognito (tarefa 2.5).

Provisiona o Amazon Cognito responsável pela autenticação do usuário descrita
no ``design.md`` (cadastro, login, MFA e emissão de tokens JWT). É implementado
como um :class:`~constructs.Construct` separado (e não diretamente na
``SecurityStack``) para reduzir conflito de edição com outras tarefas da mesma
camada de segurança.

Recursos criados:

- **User Pool** com self sign-up, verificação de e-mail, política de senha e
  MFA opcional via aplicativo autenticador (TOTP), emitindo tokens JWT.
- **Atributos customizados** de Perfil_Investidor coletados no cadastro:
  ``nivel_conhec`` (nível de conhecimento financeiro) e ``perfil_risco``
  (tolerância a risco), alinhados às enumerações do ``design.md``.
- **App Client** público (sem secret) para integração do frontend
  (React/Next.js + AWS Amplify), com fluxo SRP e erros de existência de usuário
  ocultados.

Os identificadores (``userPoolId`` e ``clientId``) são expostos como
``CfnOutput`` para servir de contrato ao frontend, que está fora do escopo do
backend.

Requisitos cobertos: 1.1, 1.4, 2.1, 2.4, 12.3.
"""

from __future__ import annotations

from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_cognito as cognito
from constructs import Construct

from config import ConfigAmbiente

# Valores permitidos dos atributos de perfil, alinhados ao ``design.md``.
# O Cognito não valida enumerações em atributos de string; a validação de
# domínio é feita na camada de aplicação (Lambdas/``shared``). Os valores ficam
# documentados aqui para manter o contrato consistente com o design.
#
# ``perfil_risco``: enum Perfil_Risco do design (tolerância a risco).
PERFIS_RISCO = ("CONSERVADOR", "MODERADO", "ARROJADO")
# ``nivel_conhec``: nível de conhecimento financeiro (regras do Agente_Explicador
# do design: perfil básico recebe conteúdo educativo; perfil avançado, técnico).
NIVEIS_CONHECIMENTO = ("BASICO", "INTERMEDIARIO", "AVANCADO")


class CognitoConstruct(Construct):
    """Cria o Cognito User Pool e o App Client do frontend.

    Exposto pela ``SecurityStack``. Publica ``user_pool`` e ``user_pool_client``
    como atributos para wiring posterior (ex.: validação de JWT no AgentCore
    Gateway, tarefa 9.2).
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        config: ConfigAmbiente,
    ) -> None:
        super().__init__(scope, construct_id)
        self.config = config

        # Em ambientes não-prod o pool pode ser destruído com a stack, agilizando
        # iterações; em prod é retido para evitar perda acidental de usuários.
        removal_policy = (
            RemovalPolicy.RETAIN if config.nome == "prod" else RemovalPolicy.DESTROY
        )

        self.user_pool = self._criar_user_pool(removal_policy)
        self.user_pool_client = self._criar_app_client()

        self._exportar_contratos()

    def _criar_user_pool(self, removal_policy: RemovalPolicy) -> cognito.UserPool:
        """Cria o User Pool com cadastro, verificação de e-mail, senha e MFA."""
        return cognito.UserPool(
            self,
            "UserPool",
            user_pool_name=self.config.nome_recurso("maia-user-pool"),
            # Cadastro self-service (Requisitos 1.1, 1.4).
            self_sign_up_enabled=True,
            # Login por e-mail; e-mail também é usado como nome de usuário.
            sign_in_aliases=cognito.SignInAliases(email=True),
            sign_in_case_sensitive=False,
            # Verificação de e-mail no cadastro (confirma identidade do usuário).
            auto_verify=cognito.AutoVerifiedAttrs(email=True),
            user_verification=cognito.UserVerificationConfig(
                email_subject="Confirme seu cadastro no GEVI Investimentos",
                email_body=(
                    "Obrigado por se cadastrar no GEVI Investimentos. Seu código "
                    "de verificação é {####}."
                ),
                email_style=cognito.VerificationEmailStyle.CODE,
            ),
            # Atributos padrão coletados no cadastro.
            standard_attributes=cognito.StandardAttributes(
                email=cognito.StandardAttribute(required=True, mutable=True),
                fullname=cognito.StandardAttribute(required=False, mutable=True),
            ),
            # Atributos de Perfil_Investidor (Requisito 1.4). Strings mutáveis;
            # o domínio de valores é validado na aplicação (ver constantes acima).
            custom_attributes={
                "nivel_conhec": cognito.StringAttribute(
                    min_len=5, max_len=20, mutable=True
                ),
                "perfil_risco": cognito.StringAttribute(
                    min_len=8, max_len=12, mutable=True
                ),
            },
            # Política de senha (dados de acesso protegidos — Requisito 12.3).
            password_policy=cognito.PasswordPolicy(
                min_length=8,
                require_lowercase=True,
                require_uppercase=True,
                require_digits=True,
                require_symbols=True,
                temp_password_validity=Duration.days(3),
            ),
            # MFA opcional via aplicativo autenticador (TOTP), sem custo de SMS.
            # Reforça a confirmação de identidade em operações sensíveis
            # (Requisito 12.3) sem obrigar todos os usuários.
            mfa=cognito.Mfa.OPTIONAL,
            mfa_second_factor=cognito.MfaSecondFactor(otp=True, sms=False),
            # Recuperação de conta apenas por e-mail verificado.
            account_recovery=cognito.AccountRecovery.EMAIL_ONLY,
            removal_policy=removal_policy,
        )

    def _criar_app_client(self) -> cognito.UserPoolClient:
        """Cria o App Client público para o frontend (contrato exposto).

        Cliente sem secret (SPA React/Next.js + Amplify), com fluxo SRP e
        validade de tokens JWT explícita. ``prevent_user_existence_errors``
        evita revelar qual campo está incorreto no login (Requisito 2.4/2.2).
        """
        return self.user_pool.add_client(
            "FrontendClient",
            user_pool_client_name=self.config.nome_recurso("maia-frontend-client"),
            # Cliente público: não gera secret (adequado a apps de navegador).
            generate_secret=False,
            auth_flows=cognito.AuthFlow(user_srp=True),
            prevent_user_existence_errors=True,
            # Validade dos tokens JWT emitidos após o login (Requisito 2.1).
            access_token_validity=Duration.hours(1),
            id_token_validity=Duration.hours(1),
            refresh_token_validity=Duration.days(30),
            # Atributos que o cliente pode ler/gravar (perfil do investidor).
            read_attributes=cognito.ClientAttributes()
            .with_standard_attributes(email=True, email_verified=True, fullname=True)
            .with_custom_attributes("nivel_conhec", "perfil_risco"),
            write_attributes=cognito.ClientAttributes()
            .with_standard_attributes(email=True, fullname=True)
            .with_custom_attributes("nivel_conhec", "perfil_risco"),
        )

    def _exportar_contratos(self) -> None:
        """Publica ``userPoolId`` e ``clientId`` como CfnOutput (contrato do frontend)."""
        stack_nome = Stack.of(self).stack_name

        CfnOutput(
            self,
            "UserPoolId",
            value=self.user_pool.user_pool_id,
            description="ID do Cognito User Pool (contrato de autenticação do frontend).",
            export_name=f"{stack_nome}-UserPoolId",
        )
        CfnOutput(
            self,
            "UserPoolClientId",
            value=self.user_pool_client.user_pool_client_id,
            description="ID do App Client do frontend (integração via Amplify).",
            export_name=f"{stack_nome}-UserPoolClientId",
        )
        CfnOutput(
            self,
            "UserPoolArn",
            value=self.user_pool.user_pool_arn,
            description="ARN do User Pool (validação de JWT no AgentCore Gateway).",
            export_name=f"{stack_nome}-UserPoolArn",
        )
