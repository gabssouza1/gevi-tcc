"""Stack de borda (edge) — distribuição do frontend com CloudFront + OAC + WAF.

Implementa a tarefa 2.4 do plano (Requisito 12.1): entrega global dos assets do
frontend hospedados em ``s3-investimentos/frontend/`` por meio de uma
distribuição Amazon CloudFront, com acesso ao bucket privado via Origin Access
Control (OAC), proteção do AWS WAF (WAFv2) e HTTPS obrigatório para os dados em
trânsito (design.md — camadas "Proteção de rede", "Dados em trânsito" e "Acesso
ao S3").

Decisão de arquitetura (por que uma stack dedicada):

O bucket ``s3-investimentos`` vive na ``DataStack``, que já depende da
``SecurityStack`` pela chave KMS. Colocar o CloudFront na ``SecurityStack``
referenciando o bucket da ``DataStack`` criaria o ciclo
``Security -> Data -> Security``. Para evitar isso, o CloudFront/OAC/WAF fica
nesta stack dedicada, que recebe o bucket por parâmetro via ``app.py``. A ordem
de dependências resultante é linear: ``Security -> Data -> Frontend``.

Sobre a dependência circular clássica do OAC (bucket <-> distribuição <-> chave
KMS): ao ligar ``S3BucketOrigin.with_origin_access_control`` a um bucket
*owned* de outra stack criptografado por CMK, o CDK adiciona à política do
bucket (na ``DataStack``) e à política da chave (na ``SecurityStack``) o ARN
**concreto** da distribuição — criando as arestas ``Data -> Frontend`` e
``Security -> Frontend`` que, somadas a ``Frontend -> Data -> Security``, fecham
um ciclo de dependência entre stacks e quebram o ``cdk synth``.

Para evitar isso, esta stack recebe apenas o **nome** do bucket
(``nome_bucket_investimentos``) e o importa por atributos
(``Bucket.from_bucket_attributes``). Sobre um bucket importado, o CDK não muta a
política (nem do bucket nem da chave): ele apenas emite um aviso. A autorização
do CloudFront (leitura do prefixo ``frontend/`` e uso da chave KMS) é concedida
na ``DataStack`` usando um **curinga** de ID de distribuição
(``arn:aws:cloudfront::<conta>:distribution/*``) na condição ``AWS:SourceArn``,
exatamente como o repositório já faz para o CloudTrail
(ver ``data_stack._autorizar_cloudtrail_na_chave`` e
``data_stack._autorizar_cloudfront_frontend``). Como o nome do bucket é uma
string literal (não um token) e as políticas usam curinga, não há referência
reversa entre as stacks; a ordem de deploy é garantida por uma dependência
explícita de stack (``frontend.add_dependency(dados)`` em ``app.py``).

Requisitos cobertos pela tarefa: 12.1.
"""

from __future__ import annotations

from aws_cdk import Stack
from aws_cdk import aws_cloudfront as cloudfront
from aws_cdk import aws_cloudfront_origins as origins
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_wafv2 as wafv2
from constructs import Construct

from config import ConfigAmbiente

# Limite de requisições por IP na janela de 5 minutos usado pela regra de
# rate limiting do WAF (mitigação de DDoS/força bruta na camada 7). É o menor
# valor permitido pelo WAFv2 para regras baseadas em taxa.
LIMITE_REQUISICOES_POR_IP = 2000

# Prefixo lógico do bucket que contém os assets do frontend (design.md).
ORIGIN_PATH_FRONTEND = "/frontend"

# Objeto raiz padrão servido quando a requisição aponta para a raiz da origem.
OBJETO_RAIZ_PADRAO = "index.html"


class FrontendStack(Stack):
    """Provisiona CloudFront + OAC + WAF para servir o frontend privado no S3.

    Recebe o bucket ``s3-investimentos`` (criado na ``DataStack``) por parâmetro
    e o expõe globalmente via CloudFront, mantendo o bucket privado (acesso
    exclusivo do CloudFront através do OAC).

    Atributos:
        web_acl: Web ACL do WAFv2 (escopo ``CLOUDFRONT``) anexada à distribuição.
        distribuicao: Distribuição CloudFront que serve ``frontend/`` via OAC.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        config: ConfigAmbiente,
        nome_bucket_investimentos: str,
        domain_names: list[str] | None = None,
        certificate_arn: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.config = config

        # Bucket importado por nome (string literal): evita que o CDK mute a
        # política do bucket/chave com o ARN concreto da distribuição (o que
        # criaria o ciclo entre stacks). A autorização do CloudFront é feita na
        # DataStack com curinga de distribuição. Ver docstring do módulo.
        bucket_investimentos = s3.Bucket.from_bucket_attributes(
            self,
            "BucketInvestimentosRef",
            bucket_name=nome_bucket_investimentos,
            region=self.region,
        )

        # WAF primeiro: o ARN da Web ACL é referenciado pela distribuição.
        self.web_acl = self._criar_web_acl()
        self.distribuicao = self._criar_distribuicao(
            bucket_investimentos,
            domain_names=domain_names,
            certificate_arn=certificate_arn,
        )

    def _criar_web_acl(self) -> wafv2.CfnWebACL:
        """Cria a Web ACL do WAFv2 no escopo ``CLOUDFRONT``.

        O escopo ``CLOUDFRONT`` exige que a Web ACL exista em ``us-east-1`` —
        região de deploy deste projeto (design.md), portanto compatível. A ação
        padrão é ``allow`` (permite o tráfego que não casar com nenhuma regra de
        bloqueio), e as regras gerenciadas/base cobrem as ameaças do design:

        - ``AWSManagedRulesCommonRuleSet``: exploits web comuns, incluindo XSS.
        - ``AWSManagedRulesKnownBadInputsRuleSet``: payloads maliciosos e
          entradas sabidamente inválidas.
        - ``AWSManagedRulesSQLiRuleSet``: injeção de SQL (SQL injection).
        - ``AWSManagedRulesAmazonIpReputationList``: IPs de má reputação
          (bots e origens associadas a ataques/DDoS).
        - Regra baseada em taxa (rate limiting): bloqueia IPs que excederem
          ``LIMITE_REQUISICOES_POR_IP`` requisições em 5 minutos (mitigação de
          DDoS/força bruta na camada de aplicação).
        """
        regras = [
            self._regra_gerenciada("AWSManagedRulesCommonRuleSet", 1),
            self._regra_gerenciada("AWSManagedRulesKnownBadInputsRuleSet", 2),
            self._regra_gerenciada("AWSManagedRulesSQLiRuleSet", 3),
            self._regra_gerenciada("AWSManagedRulesAmazonIpReputationList", 4),
            self._regra_rate_limit(5),
        ]

        nome = self.config.nome_recurso("maia-frontend-webacl")
        return wafv2.CfnWebACL(
            self,
            "WebAclFrontend",
            name=nome,
            scope="CLOUDFRONT",
            default_action=wafv2.CfnWebACL.DefaultActionProperty(allow={}),
            visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                cloud_watch_metrics_enabled=True,
                sampled_requests_enabled=True,
                metric_name=nome,
            ),
            rules=regras,
        )

    def _regra_gerenciada(
        self, nome_grupo: str, prioridade: int
    ) -> wafv2.CfnWebACL.RuleProperty:
        """Monta uma regra que usa um grupo de regras gerenciadas da AWS.

        Grupos gerenciados usam ``override_action = none`` para preservar as
        ações internas do grupo (bloqueio/contagem definidos pela AWS), em vez
        de sobrescrevê-las. As métricas do CloudWatch ficam habilitadas para
        observabilidade das detecções.
        """
        return wafv2.CfnWebACL.RuleProperty(
            name=nome_grupo,
            priority=prioridade,
            override_action=wafv2.CfnWebACL.OverrideActionProperty(none={}),
            statement=wafv2.CfnWebACL.StatementProperty(
                managed_rule_group_statement=(
                    wafv2.CfnWebACL.ManagedRuleGroupStatementProperty(
                        vendor_name="AWS",
                        name=nome_grupo,
                    )
                ),
            ),
            visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                cloud_watch_metrics_enabled=True,
                sampled_requests_enabled=True,
                metric_name=nome_grupo,
            ),
        )

    def _regra_rate_limit(self, prioridade: int) -> wafv2.CfnWebACL.RuleProperty:
        """Monta a regra baseada em taxa (rate limiting) para mitigar DDoS.

        Bloqueia (``action = block``) qualquer IP de origem que ultrapassar o
        limite de requisições na janela móvel de 5 minutos, agregando por
        endereço IP.
        """
        nome = "LimiteDeTaxaPorIP"
        return wafv2.CfnWebACL.RuleProperty(
            name=nome,
            priority=prioridade,
            action=wafv2.CfnWebACL.RuleActionProperty(block={}),
            statement=wafv2.CfnWebACL.StatementProperty(
                rate_based_statement=(
                    wafv2.CfnWebACL.RateBasedStatementProperty(
                        limit=LIMITE_REQUISICOES_POR_IP,
                        aggregate_key_type="IP",
                    )
                ),
            ),
            visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                cloud_watch_metrics_enabled=True,
                sampled_requests_enabled=True,
                metric_name=nome,
            ),
        )


    def _criar_distribuicao(
        self,
        bucket_investimentos: s3.IBucket,
        *,
        domain_names: list[str] | None,
        certificate_arn: str | None,
    ) -> cloudfront.Distribution:
        """Cria a distribuição CloudFront servindo ``frontend/`` via OAC.

        Características (design.md — camadas de proteção de rede e dados em
        trânsito):

        - Origem S3 com **Origin Access Control (OAC)**, não o legado OAI:
          ``S3BucketOrigin.with_origin_access_control`` cria o OAC e liga a
          distribuição ao bucket. Como o bucket é **importado por nome**, o CDK
          não muta aqui a política do bucket nem a da chave KMS (apenas emite um
          aviso); essas autorizações — leitura do prefixo ``frontend/`` e uso da
          chave — são concedidas na ``DataStack`` com curinga de distribuição
          (ver ``data_stack._autorizar_cloudfront_frontend``). O bucket permanece
          privado (``BLOCK_ALL``); o acesso ao ``frontend/`` é feito só pelo
          CloudFront.
        - ``origin_path = /frontend``: a origem aponta para o prefixo lógico dos
          assets do frontend dentro do ``s3-investimentos``.
        - ``viewer_protocol_policy = REDIRECT_TO_HTTPS``: força HTTPS para todo o
          tráfego de visualizador, garantindo criptografia em trânsito.
        - ``default_root_object = index.html``: objeto servido na raiz.
        - Web ACL do WAF anexada via ``web_acl_id`` (ARN, exigido no escopo
          ``CLOUDFRONT`` do WAFv2).

        TLS 1.2+ (Requisito 12.1): o ``minimum_protocol_version`` de uma
        distribuição só é aplicável quando há certificado próprio (domínio
        customizado). Quando ``domain_names``/``certificate_arn`` são informados,
        fixamos o piso ``TLSv1.2_2021``. Sem domínio customizado, a distribuição
        usa o certificado padrão ``*.cloudfront.net`` (cuja política de segurança
        é gerenciada pela AWS) e o ``REDIRECT_TO_HTTPS`` mantém todo o tráfego
        cifrado; o piso rígido de TLS 1.2 passa a valer ao anexar um certificado
        ACM pelos parâmetros desta stack.
        """
        origem = origins.S3BucketOrigin.with_origin_access_control(
            bucket_investimentos,
            origin_path=ORIGIN_PATH_FRONTEND,
        )

        # O frontend é um export estático do Next.js com ``trailingSlash`` —
        # cada rota vira um "diretório" com ``index.html`` (ex.: ``/login/`` ->
        # ``login/index.html``). O CloudFront só resolve ``default_root_object``
        # na raiz da distribuição; para subcaminhos, um pedido a ``/login/`` iria
        # ao S3 como o prefixo ``frontend/login/`` (que não é um objeto) e
        # retornaria NoSuchKey. Esta CloudFront Function (evento
        # ``viewer-request``) reescreve o URI para o ``index.html`` correto:
        # sufixo ``/`` -> ``.../index.html`` e caminhos sem extensão (ex.:
        # ``/login``) -> ``.../index.html``. Assets com extensão (``.js``,
        # ``.css``, imagens) passam sem alteração.
        funcao_rewrite = cloudfront.Function(
            self,
            "RewriteUriParaIndex",
            comment=self.config.nome_recurso("maia-frontend-rewrite"),
            code=cloudfront.FunctionCode.from_inline(
                "function handler(event) {\n"
                "  var request = event.request;\n"
                "  var uri = request.uri;\n"
                "  if (uri.endsWith('/')) {\n"
                "    request.uri += 'index.html';\n"
                "  } else if (!uri.includes('.')) {\n"
                "    request.uri += '/index.html';\n"
                "  }\n"
                "  return request;\n"
                "}\n"
            ),
        )

        comportamento_padrao = cloudfront.BehaviorOptions(
            origin=origem,
            viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
            allowed_methods=cloudfront.AllowedMethods.ALLOW_GET_HEAD_OPTIONS,
            cached_methods=cloudfront.CachedMethods.CACHE_GET_HEAD_OPTIONS,
            cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
            compress=True,
            function_associations=[
                cloudfront.FunctionAssociation(
                    function=funcao_rewrite,
                    event_type=cloudfront.FunctionEventType.VIEWER_REQUEST,
                )
            ],
        )

        props: dict = {
            "comment": self.config.nome_recurso("maia-frontend"),
            "default_root_object": OBJETO_RAIZ_PADRAO,
            "default_behavior": comportamento_padrao,
            "web_acl_id": self.web_acl.attr_arn,
            "minimum_protocol_version": (
                cloudfront.SecurityPolicyProtocol.TLS_V1_2_2021
            ),
            "http_version": cloudfront.HttpVersion.HTTP2_AND_3,
            "enabled": True,
        }

        # Domínio customizado + certificado ACM (opcional): habilita o piso
        # rígido de TLS 1.2 no certificado do visualizador. Em dev, ambos são
        # ``None`` e a distribuição usa o certificado padrão do CloudFront.
        if domain_names and certificate_arn:
            from aws_cdk import aws_certificatemanager as acm

            props["domain_names"] = domain_names
            props["certificate"] = acm.Certificate.from_certificate_arn(
                self, "CertificadoFrontend", certificate_arn
            )

        return cloudfront.Distribution(self, "DistribuicaoFrontend", **props)
