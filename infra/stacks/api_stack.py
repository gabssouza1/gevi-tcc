"""Stack de API — proxy HTTP entre o frontend e o AgentCore (opção "b").

O frontend é um export estático (S3 + CloudFront) autenticado no Cognito. Este
HTTP API (Amazon API Gateway v2), autorizado pelo mesmo Cognito, coloca uma
Lambda proxy (``fn-api-proxy``) na frente do AgentCore e repassa o *access token*
do usuário:

- ``POST /chat``           → Runtime dos 5 agentes (pipeline completo).
- ``POST /gateway/{tool}`` → tools do Gateway (MCP): indicadores, portfólio,
  histórico, perfil e simulação (dados reais).

Motivação:
- CORS: o Gateway/Runtime do AgentCore não respondem a chamadas cross-origin de
  navegador; o HTTP API expõe CORS para a origem do CloudFront.
- Auth: o Runtime usa *inbound auth* por JWT; a AWS documenta que, nesse modo, a
  invocação é uma requisição HTTPS com ``Authorization: Bearer <token>`` (não
  SDK/SigV4). Por isso a Lambda apenas repassa o token — não precisa de IAM sobre
  o Runtime/Gateway (a validação do JWT é feita pelo próprio AgentCore).

O authorizer JWT do HTTP API valida o token do Cognito na entrada (mesma pool/
client do Gateway e do Runtime), garantindo que só requisições autenticadas
alcancem a Lambda. A Lambda deriva o ``user_id`` do ``sub`` do JWT (nunca do
corpo), preservando o isolamento por usuário.

Dependências de deploy: Security (Cognito) e Agents (Gateway URL + Runtime ARN).
"""

from __future__ import annotations

from pathlib import Path

from aws_cdk import CfnOutput, Duration, Stack
from aws_cdk import aws_apigatewayv2 as apigwv2
from aws_cdk import aws_apigatewayv2_authorizers as apigwv2_authz
from aws_cdk import aws_apigatewayv2_integrations as apigwv2_int
from aws_cdk import aws_cognito as cognito
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from constructs import Construct

from config import ConfigAmbiente

# Diretório-fonte da Lambda proxy (apenas stdlib; sem shared nem dependências).
_DIR_PROXY = Path(__file__).resolve().parent.parent.parent / "lambdas" / "fn-api-proxy"

RUNTIME = lambda_.Runtime.PYTHON_3_12
HANDLER = "handler.handler"
# O chat aciona 5 agentes + LLM; o teto de integração do HTTP API é 30s.
TIMEOUT_LAMBDA = Duration.seconds(59)


class ApiStack(Stack):
    """HTTP API + Lambda proxy que repassa o JWT do usuário ao AgentCore.

    Atributos:
        api: o HTTP API (Amazon API Gateway v2).
        funcao_proxy: a Lambda ``fn-api-proxy``.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        config: ConfigAmbiente,
        user_pool: cognito.IUserPool,
        user_pool_client: cognito.IUserPoolClient,
        gateway_url: str,
        allowed_origin: str,
        runtime_arn: str | None = None,
        runtime_qualifier: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.config = config

        self.funcao_proxy = self._criar_lambda_proxy(
            gateway_url=gateway_url,
            runtime_arn=runtime_arn,
            runtime_qualifier=runtime_qualifier,
        )
        self.api = self._criar_http_api(
            user_pool=user_pool,
            user_pool_client=user_pool_client,
            allowed_origin=allowed_origin,
        )

        CfnOutput(
            self,
            "SaidaApiUrl",
            value=self.api.api_endpoint,
            description="URL base do HTTP API (proxy do frontend para o AgentCore).",
        )

    def _criar_lambda_proxy(
        self,
        *,
        gateway_url: str,
        runtime_arn: str | None,
        runtime_qualifier: str | None,
    ) -> lambda_.Function:
        """Cria a Lambda proxy (Python 3.12, apenas stdlib).

        Sem role customizada: as chamadas ao Gateway/Runtime são HTTPS com o JWT
        do usuário (não IAM), então a função só precisa da role gerenciada padrão
        (logs no CloudWatch). Logs em JSON e X-Ray ativos para observabilidade.
        """
        ambiente = {
            "GATEWAY_URL": gateway_url,
            "REGIAO": self.region,
        }
        if runtime_arn:
            ambiente["RUNTIME_ARN"] = runtime_arn
        if runtime_qualifier:
            ambiente["RUNTIME_QUALIFIER"] = runtime_qualifier

        return lambda_.Function(
            self,
            "FnApiProxy",
            function_name=self.config.nome_recurso("fn-api-proxy"),
            runtime=RUNTIME,
            handler=HANDLER,
            code=lambda_.Code.from_asset(str(_DIR_PROXY)),
            environment=ambiente,
            timeout=TIMEOUT_LAMBDA,
            memory_size=256,
            logging_format=lambda_.LoggingFormat.JSON,
            application_log_level_v2=lambda_.ApplicationLogLevel.INFO,
            tracing=lambda_.Tracing.ACTIVE,
            log_retention=logs.RetentionDays.ONE_MONTH,
            description=(
                "Proxy HTTP do frontend para o AgentCore (Gateway tools + Runtime), "
                f"repassando o JWT do Cognito (ambiente {self.config.nome})."
            ),
        )


    def _criar_http_api(
        self,
        *,
        user_pool: cognito.IUserPool,
        user_pool_client: cognito.IUserPoolClient,
        allowed_origin: str,
    ) -> apigwv2.HttpApi:
        """Cria o HTTP API com authorizer JWT do Cognito, CORS e as rotas.

        - Authorizer JWT: ``issuer`` = endpoint do User Pool, ``audience`` = App
          Client. O frontend envia o *access token* do Cognito (claim
          ``client_id``); o HTTP API valida ``client_id`` contra a audience
          quando ``aud`` está ausente (comportamento do authorizer JWT para
          tokens de acesso do Cognito).
        - CORS: libera a origem do CloudFront (o front estático), métodos POST/
          OPTIONS e o header ``Authorization``.
        - Rotas: ``POST /chat`` e ``POST /gateway/{tool}`` integradas à Lambda.
        """
        issuer = (
            f"https://cognito-idp.{self.region}.amazonaws.com/"
            f"{user_pool.user_pool_id}"
        )
        authorizer = apigwv2_authz.HttpJwtAuthorizer(
            "AuthorizerCognito",
            jwt_issuer=issuer,
            identity_source=["$request.header.Authorization"],
            jwt_audience=[user_pool_client.user_pool_client_id],
        )

        api = apigwv2.HttpApi(
            self,
            "HttpApi",
            api_name=self.config.nome_recurso("maia-api"),
            description=(
                "HTTP API (proxy) do frontend para o AgentCore: /chat (Runtime) e "
                "/gateway/{tool} (tools do Gateway)."
            ),
            cors_preflight=apigwv2.CorsPreflightOptions(
                allow_origins=[allowed_origin],
                allow_methods=[
                    apigwv2.CorsHttpMethod.POST,
                    apigwv2.CorsHttpMethod.OPTIONS,
                ],
                allow_headers=["authorization", "content-type"],
                max_age=Duration.hours(1),
            ),
        )

        integracao = apigwv2_int.HttpLambdaIntegration(
            "IntegracaoProxy", handler=self.funcao_proxy
        )

        api.add_routes(
            path="/chat",
            methods=[apigwv2.HttpMethod.POST],
            integration=integracao,
            authorizer=authorizer,
        )
        api.add_routes(
            path="/gateway/{tool}",
            methods=[apigwv2.HttpMethod.POST],
            integration=integracao,
            authorizer=authorizer,
        )
        return api
