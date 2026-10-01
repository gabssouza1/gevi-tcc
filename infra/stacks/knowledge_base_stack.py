"""Stack do Knowledge Base (RAG) — OpenSearch Serverless + Bedrock KB.

Recursos implementados nesta etapa e nas tarefas seguintes do plano:

- Tarefa 8.1 (implementada) — Coleção vetorial OpenSearch Serverless
  (``VECTORSEARCH``) com as três políticas obrigatórias do serviço: criptografia
  (encryption), rede (network) e acesso a dados (data access). A especificação do
  índice de embeddings (nome, dimensão, motor e nomes de campos) é definida e
  exposta em ``spec_indice`` como ponto único de verdade.
- Tarefa 8.2 (implementada) — Bedrock Knowledge Base apontando para
  ``s3-investimentos/kb-documentos/``: role de serviço do KB (menor privilégio),
  índice vetorial declarativo (``CfnIndex``), a própria Knowledge Base
  (``CfnKnowledgeBase``) com armazenamento no OpenSearch Serverless e a data
  source S3 (``CfnDataSource``) com chunking de ~512 tokens. A política de acesso
  a dados do AOSS foi refinada para autorizar a role do KB.

A ingestão dos documentos (tarefa 8.3) é operacional/automatizada.

Decisão — Criptografia (encryption policy):
    A política de criptografia usa a chave gerenciada pela AWS
    (``AWSOwnedKey``) em vez da CMK provisionada na ``SecurityStack``. Isso
    evita, nesta etapa, uma dependência cross-stack (a ``KnowledgeBaseStack`` não
    recebe a chave KMS hoje — ver ``app.py``) e mantém a camada autocontida.

Decisão — Índice vetorial (por que ``CfnIndex`` e por que o piso de versão subiu):
    O Bedrock Knowledge Base com armazenamento OpenSearch Serverless EXIGE que o
    índice vetorial já exista no momento da criação da KB (ver
    https://docs.aws.amazon.com/bedrock/latest/userguide/knowledge-base-setup-oss.html).
    ``CfnCollection`` cria a coleção, mas não cria índices. A forma declarativa
    de materializar o índice é o recurso ``AWS::OpenSearchServerless::Index``
    (CDK ``aws_opensearchserverless.CfnIndex``), disponível a partir de versões
    do ``aws-cdk-lib`` mais recentes que o piso original (``>=2.161.0``). Para
    manter a solução coerente e verificável — índice criado ANTES da KB, com
    dependência explícita, e sem introduzir uma Lambda de custom resource — o
    piso em ``infra/requirements.txt`` foi elevado para uma versão que inclui
    ``CfnIndex`` (a garantia de OAC cross-stack, ``>=2.161.0``, continua
    satisfeita). O índice usa EXATAMENTE ``spec_indice`` (campos e dimensão),
    mantendo um único ponto de verdade entre o índice e a configuração da KB.

Decisão — Modelo de embeddings:
    ``amazon.titan-embed-text-v2:0`` (dimensão 1024), coerente com
    ``spec_indice.dimensao``.

Requisitos cobertos pela camada: 5.1, 5.3, 5.4, 10.3.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from aws_cdk import Stack
from aws_cdk import aws_bedrock as bedrock
from aws_cdk import aws_iam as iam
from aws_cdk import aws_opensearchserverless as aoss
from aws_cdk import aws_s3 as s3
from constructs import Construct

from config import ConfigAmbiente

# Modelo de embeddings do Knowledge Base (Amazon Titan Text Embeddings V2).
# Dimensão 1024 — coerente com ``EspecIndiceVetorial.dimensao``.
MODELO_EMBEDDINGS = "amazon.titan-embed-text-v2:0"

# Chunking de ingestão (~512 tokens por chunk), conforme design.md ("Chunking:
# ~512 tokens por chunk"). Overlap moderado entre chunks adjacentes para
# preservar contexto nas bordas.
MAX_TOKENS_CHUNK = 512
OVERLAP_PERCENTUAL_CHUNK = 20

# Prefixo do bucket ``s3-investimentos`` indexado pela Knowledge Base.
PREFIXO_KB_DOCUMENTOS = "kb-documentos/"


@dataclass(frozen=True)
class EspecIndiceVetorial:
    """Especificação do índice vetorial de embeddings do Knowledge Base.

    Ponto único de verdade compartilhado entre a coleção, o índice físico
    (``CfnIndex``) e a configuração do Bedrock Knowledge Base
    (``CfnKnowledgeBase``). Os nomes de campos seguem o padrão esperado pelo
    Bedrock Knowledge Bases ao usar OpenSearch Serverless como armazenamento
    vetorial.

    Atributos:
        nome_indice: Nome do índice vetorial materializado na coleção.
        dimensao: Dimensão dos vetores de embedding. 1024 é a dimensão padrão do
            Amazon Titan Text Embeddings V2 (``amazon.titan-embed-text-v2:0``),
            o modelo de embeddings configurado nesta camada.
        campo_vetorial: Campo ``knn_vector`` que armazena o embedding.
        campo_texto: Campo com o trecho de texto (chunk) original.
        campo_metadados: Campo com os metadados de origem do trecho.
        motor: Motor de busca vetorial (``faiss``).
        tipo_distancia: Métrica de distância/similaridade (``l2``).
    """

    nome_indice: str
    dimensao: int = 1024
    campo_vetorial: str = "bedrock-knowledge-base-default-vector"
    campo_texto: str = "AMAZON_BEDROCK_TEXT_CHUNK"
    campo_metadados: str = "AMAZON_BEDROCK_METADATA"
    motor: str = "faiss"
    tipo_distancia: str = "l2"


class KnowledgeBaseStack(Stack):
    """Provisiona a coleção OpenSearch Serverless e o Bedrock Knowledge Base.

    Provisiona a coleção vetorial ``VECTORSEARCH`` e as três políticas
    obrigatórias do OpenSearch Serverless (tarefa 8.1) e, sobre elas, a role de
    serviço do KB, o índice vetorial, a Bedrock Knowledge Base e a data source
    S3 apontando para ``kb-documentos/`` (tarefa 8.2).

    Atributos:
        bucket_investimentos: Bucket ``s3-investimentos`` (recebido da
            ``DataStack``) cujo prefixo ``kb-documentos/`` é a fonte da KB.
        colecao: Coleção OpenSearch Serverless (``CfnCollection``) do tipo
            ``VECTORSEARCH``, usada como armazenamento vetorial.
        colecao_arn: ARN da coleção.
        colecao_endpoint: Endpoint da coleção (usado para criar o índice).
        spec_indice: Especificação do índice de embeddings
            (``EspecIndiceVetorial``), ponto único de verdade dos campos/dimensão.
        role_kb: Role de serviço do Bedrock Knowledge Base (menor privilégio).
        indice: Índice vetorial (``CfnIndex``) criado antes da KB.
        knowledge_base: Bedrock Knowledge Base (``CfnKnowledgeBase``).
        data_source: Data source S3 (``CfnDataSource``) do prefixo
            ``kb-documentos/`` com chunking de ~512 tokens.
        knowledge_base_id: ID da Knowledge Base, exposto para as tarefas 8.3/9.x.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        config: ConfigAmbiente,
        bucket_investimentos: s3.IBucket,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.config = config
        self.bucket_investimentos = bucket_investimentos

        # Nome lógico da coleção (padrão AOSS: minúsculo, 3-32 chars).
        nome_colecao = self._nome_aoss("maia-kb")

        # Especificação do índice de embeddings (ponto único de verdade).
        self.spec_indice = EspecIndiceVetorial(
            nome_indice=self._nome_aoss("maia-kb-idx"),
        )

        # Role de serviço do KB criada ANTES das políticas: a política de acesso
        # a dados do AOSS referencia o ARN da role. As permissões que dependem do
        # ARN da coleção (aoss:APIAccessAll) são anexadas após a criação da
        # coleção, evitando dependência circular.
        self.role_kb = self._criar_role_servico_kb()

        # Tarefa 8.1 — Políticas obrigatórias do AOSS (antes da coleção).
        politica_cripto = self._criar_politica_criptografia(nome_colecao)
        politica_rede = self._criar_politica_rede(nome_colecao)
        politica_acesso = self._criar_politica_acesso_dados(
            nome_colecao, self.role_kb.role_arn
        )

        # Tarefa 8.1 — Coleção vetorial (VECTORSEARCH).
        self.colecao = self._criar_colecao(nome_colecao)
        self.colecao.add_dependency(politica_cripto)
        self.colecao.add_dependency(politica_rede)
        self.colecao.add_dependency(politica_acesso)

        self.colecao_arn = self.colecao.attr_arn
        self.colecao_endpoint = self.colecao.attr_collection_endpoint

        # Acesso da role do KB à coleção (data plane do AOSS). Depende do ARN da
        # coleção, por isso é anexado após a criação da coleção.
        self.role_kb.add_to_policy(
            iam.PolicyStatement(
                sid="AcessoColecaoAOSS",
                effect=iam.Effect.ALLOW,
                actions=["aoss:APIAccessAll"],
                resources=[self.colecao_arn],
            )
        )

        # Tarefa 8.2 — Índice vetorial (deve existir ANTES da Knowledge Base).
        self.indice = self._criar_indice_vetorial()
        self.indice.add_dependency(self.colecao)
        self.indice.add_dependency(politica_acesso)

        # Tarefa 8.2 — Bedrock Knowledge Base (storage OpenSearch Serverless).
        self.knowledge_base = self._criar_knowledge_base()
        self.knowledge_base.add_dependency(self.indice)
        self.knowledge_base.node.add_dependency(self.role_kb)

        # Tarefa 8.2 — Data source S3 apontando para kb-documentos/.
        self.data_source = self._criar_data_source()

        # Exposto para a ingestão (8.3) e para os agentes (9.x).
        self.knowledge_base_id = self.knowledge_base.attr_knowledge_base_id


    def _nome_aoss(self, base: str) -> str:
        """Monta um nome compatível com o OpenSearch Serverless.

        Nomes de coleções e políticas do AOSS devem ser minúsculos, começar por
        letra e ter de 3 a 32 caracteres (letras, números e hífens). Aplica o
        prefixo de ambiente de ``config`` (ex.: ``dev-``), força minúsculas e
        trunca em 32 caracteres para respeitar o limite do serviço.
        """
        nome = self.config.nome_recurso(base).lower()
        return nome[:32]

    def _arn_modelo_embeddings(self) -> str:
        """Monta o ARN do modelo de embeddings (foundation model do Bedrock).

        Foundation models são recursos regionais sem conta no ARN
        (``arn:<partition>:bedrock:<region>::foundation-model/<modelo>``).
        """
        return (
            f"arn:{self.partition}:bedrock:{self.region}::"
            f"foundation-model/{MODELO_EMBEDDINGS}"
        )

    def _criar_role_servico_kb(self) -> iam.Role:
        """Cria a role de serviço do Bedrock Knowledge Base (menor privilégio).

        A role é assumida pelo serviço ``bedrock.amazonaws.com`` restrito a esta
        conta e às Knowledge Bases desta conta/região (condições ``aws:SourceAccount``
        e ``aws:SourceArn``), mitigando o problema do "confused deputy".

        Permissões concedidas (apenas o necessário — Requisito 12.2):

        - ``bedrock:InvokeModel`` somente no modelo de embeddings, para converter
          os documentos e as consultas em vetores.
        - Leitura do prefixo ``kb-documentos/`` do bucket ``s3-investimentos`` via
          ``bucket.grant_read`` com padrão de chave. Como o bucket é criptografado
          por CMK (SecurityStack), o ``grant_read`` anexa automaticamente o
          ``kms:Decrypt`` necessário à role, sem que esta stack precise conhecer a
          chave diretamente.
        - ``aoss:APIAccessAll`` na coleção: anexado no construtor da stack, após a
          criação da coleção (depende do ARN dela). Além dessa permissão IAM, a
          role também é autorizada na política de acesso a dados do AOSS — o
          OpenSearch Serverless exige as duas camadas.
        """
        role = iam.Role(
            self,
            "RoleServicoKB",
            role_name=self.config.nome_recurso("maia-kb-role"),
            assumed_by=iam.ServicePrincipal(
                "bedrock.amazonaws.com",
                conditions={
                    "StringEquals": {"aws:SourceAccount": self.account},
                    "ArnLike": {
                        "aws:SourceArn": (
                            f"arn:{self.partition}:bedrock:{self.region}:"
                            f"{self.account}:knowledge-base/*"
                        )
                    },
                },
            ),
            description=(
                "Role de servico do Bedrock Knowledge Base (menor privilegio): "
                "modelo de embeddings, leitura de kb-documentos/ e coleção AOSS."
            ),
        )

        # Invocar apenas o modelo de embeddings.
        role.add_to_policy(
            iam.PolicyStatement(
                sid="InvocarModeloEmbeddings",
                effect=iam.Effect.ALLOW,
                actions=["bedrock:InvokeModel"],
                resources=[self._arn_modelo_embeddings()],
            )
        )

        # Ler somente o prefixo kb-documentos/ (inclui kms:Decrypt da CMK do bucket).
        self.bucket_investimentos.grant_read(role, f"{PREFIXO_KB_DOCUMENTOS}*")

        return role


    def _criar_politica_criptografia(
        self, nome_colecao: str
    ) -> aoss.CfnSecurityPolicy:
        """Cria a política de criptografia (encryption) da coleção.

        O OpenSearch Serverless exige uma política de criptografia associada à
        coleção antes de criá-la. Usa a chave gerenciada pela AWS
        (``AWSOwnedKey: true``) para manter a camada autocontida nesta etapa (ver
        a nota "Decisão — Criptografia" no topo do módulo).
        """
        documento = {
            "Rules": [
                {
                    "ResourceType": "collection",
                    "Resource": [f"collection/{nome_colecao}"],
                }
            ],
            "AWSOwnedKey": True,
        }
        return aoss.CfnSecurityPolicy(
            self,
            "PoliticaCriptografiaKB",
            name=self._nome_aoss("maia-kb-enc"),
            type="encryption",
            description=(
                "Politica de criptografia (chave AWS) da colecao vetorial do "
                "Knowledge Base."
            ),
            policy=json.dumps(documento),
        )

    def _criar_politica_rede(self, nome_colecao: str) -> aoss.CfnSecurityPolicy:
        """Cria a política de rede (network) da coleção.

        Permite acesso público ao endpoint da coleção e do dashboard
        (``AllowFromPublic: true``). O controle de quem pode ler/escrever é feito
        pela política de acesso a dados combinada com o IAM (a role do Bedrock
        Knowledge Base acessa a coleção com ``aoss:APIAccessAll`` e é autorizada na
        política de acesso a dados). Para restringir a rede, troque por acesso via
        VPC endpoints (``SourceVPCEs``).
        """
        documento = [
            {
                "Rules": [
                    {
                        "ResourceType": "collection",
                        "Resource": [f"collection/{nome_colecao}"],
                    },
                    {
                        "ResourceType": "dashboard",
                        "Resource": [f"collection/{nome_colecao}"],
                    },
                ],
                "AllowFromPublic": True,
            }
        ]
        return aoss.CfnSecurityPolicy(
            self,
            "PoliticaRedeKB",
            name=self._nome_aoss("maia-kb-net"),
            type="network",
            description=(
                "Politica de rede da colecao vetorial do Knowledge Base (acesso "
                "ao endpoint; autorizacao via data access + IAM)."
            ),
            policy=json.dumps(documento),
        )

    def _criar_politica_acesso_dados(
        self, nome_colecao: str, arn_role_kb: str
    ) -> aoss.CfnAccessPolicy:
        """Cria a política de acesso a dados (data access) da coleção.

        Duas entradas de menor privilégio (tarefa 8.2):

        1. Conta de deploy (principal root da conta): permissões de gerenciamento
           da coleção e do índice. Necessárias para o recurso
           ``AWS::OpenSearchServerless::Index`` criar o índice durante o deploy e
           para o job de ingestão (tarefa 8.3). O principal root da conta abrange
           as identidades IAM da conta que executam o deploy/ingestão.
        2. Role de serviço do Bedrock Knowledge Base (``arn_role_kb``): escopada
           ao que a KB precisa em tempo de execução/ingestão — descrever a coleção
           e ler/escrever/descrever documentos no índice. A KB escreve embeddings
           na ingestão e lê na busca semântica.

        O acesso a dados do AOSS é independente do IAM: a role do KB precisa desta
        autorização E da permissão IAM ``aoss:APIAccessAll`` (concedida na role).
        """
        principal_conta = f"arn:{self.partition}:iam::{self.account}:root"
        documento = [
            {
                "Rules": [
                    {
                        "ResourceType": "collection",
                        "Resource": [f"collection/{nome_colecao}"],
                        "Permission": [
                            "aoss:CreateCollectionItems",
                            "aoss:DeleteCollectionItems",
                            "aoss:UpdateCollectionItems",
                            "aoss:DescribeCollectionItems",
                        ],
                    },
                    {
                        "ResourceType": "index",
                        "Resource": [f"index/{nome_colecao}/*"],
                        "Permission": [
                            "aoss:CreateIndex",
                            "aoss:DeleteIndex",
                            "aoss:UpdateIndex",
                            "aoss:DescribeIndex",
                            "aoss:ReadDocument",
                            "aoss:WriteDocument",
                        ],
                    },
                ],
                "Principal": [principal_conta],
            },
            {
                "Rules": [
                    {
                        "ResourceType": "collection",
                        "Resource": [f"collection/{nome_colecao}"],
                        "Permission": ["aoss:DescribeCollectionItems"],
                    },
                    {
                        "ResourceType": "index",
                        "Resource": [f"index/{nome_colecao}/*"],
                        "Permission": [
                            "aoss:DescribeIndex",
                            "aoss:ReadDocument",
                            "aoss:WriteDocument",
                        ],
                    },
                ],
                "Principal": [arn_role_kb],
            },
        ]
        return aoss.CfnAccessPolicy(
            self,
            "PoliticaAcessoDadosKB",
            name=self._nome_aoss("maia-kb-data"),
            type="data",
            description=(
                "Politica de acesso a dados da colecao vetorial do Knowledge "
                "Base (conta de deploy + role de servico do Bedrock KB)."
            ),
            policy=json.dumps(documento),
        )


    def _criar_colecao(self, nome_colecao: str) -> aoss.CfnCollection:
        """Cria a coleção OpenSearch Serverless do tipo ``VECTORSEARCH``.

        O tipo ``VECTORSEARCH`` habilita a busca vetorial (k-NN) usada pelo
        Knowledge Base para busca semântica sobre os embeddings dos documentos
        de ``s3-investimentos/kb-documentos/`` (Requisitos 5.1 e 5.4). A coleção
        depende das três políticas (criptografia, rede e acesso a dados), ligadas
        via ``add_dependency`` no construtor da stack.
        """
        return aoss.CfnCollection(
            self,
            "ColecaoVetorialKB",
            name=nome_colecao,
            type="VECTORSEARCH",
            description=(
                "Colecao vetorial (indice de embeddings) do Knowledge Base do "
                "Sistema Multiagente de Investimentos."
            ),
        )

    def _criar_indice_vetorial(self) -> aoss.CfnIndex:
        """Cria o índice vetorial (``AWS::OpenSearchServerless::Index``).

        O índice é criado ANTES do Bedrock Knowledge Base (dependência explícita
        no construtor) porque a KB com armazenamento OpenSearch Serverless exige
        um índice vetorial preexistente. Usa ``spec_indice`` como ponto único de
        verdade, garantindo que os nomes de campos e a dimensão batam com a
        configuração de armazenamento da KB.

        Mapeamento (``mappings``):

        - ``campo_vetorial``: ``knn_vector`` com a dimensão do modelo de
          embeddings (1024) e método ``hnsw`` no motor ``faiss`` com distância
          ``l2`` (``spec_indice.motor``/``tipo_distancia``).
        - ``campo_texto``: ``text`` indexável (trecho original do documento).
        - ``campo_metadados``: ``text`` não indexável (metadados de origem
          armazenados, não pesquisáveis).

        ``settings.index.knn = True`` habilita a busca k-NN no índice.
        """
        spec = self.spec_indice
        return aoss.CfnIndex(
            self,
            "IndiceVetorialKB",
            collection_endpoint=self.colecao_endpoint,
            index_name=spec.nome_indice,
            mappings=aoss.CfnIndex.MappingsProperty(
                properties={
                    spec.campo_vetorial: aoss.CfnIndex.PropertyMappingProperty(
                        type="knn_vector",
                        dimension=spec.dimensao,
                        method=aoss.CfnIndex.MethodProperty(
                            name="hnsw",
                            engine=spec.motor,
                            space_type=spec.tipo_distancia,
                            parameters=aoss.CfnIndex.ParametersProperty(
                                ef_construction=512,
                                m=16,
                            ),
                        ),
                    ),
                    spec.campo_texto: aoss.CfnIndex.PropertyMappingProperty(
                        type="text",
                        index=True,
                    ),
                    spec.campo_metadados: aoss.CfnIndex.PropertyMappingProperty(
                        type="text",
                        index=False,
                    ),
                }
            ),
            settings=aoss.CfnIndex.IndexSettingsProperty(
                index=aoss.CfnIndex.IndexProperty(knn=True),
            ),
        )

    def _criar_knowledge_base(self) -> bedrock.CfnKnowledgeBase:
        """Cria o Bedrock Knowledge Base (tipo VECTOR) com storage no AOSS.

        - ``knowledge_base_configuration``: tipo ``VECTOR`` com o modelo de
          embeddings ``amazon.titan-embed-text-v2:0``.
        - ``storage_configuration``: ``OPENSEARCH_SERVERLESS`` referenciando o ARN
          da coleção, o nome do índice (``spec_indice.nome_indice``) e o mapeamento
          de campos (vetorial/texto/metadados) — os mesmos de ``spec_indice``.
        - ``role_arn``: role de serviço de menor privilégio criada nesta stack.
        """
        spec = self.spec_indice
        return bedrock.CfnKnowledgeBase(
            self,
            "KnowledgeBase",
            name=self.config.nome_recurso("maia-kb"),
            role_arn=self.role_kb.role_arn,
            description=(
                "Knowledge Base (RAG) do Sistema Multiagente de Investimentos: "
                "regulamentos, glossarios e produtos financeiros."
            ),
            knowledge_base_configuration=(
                bedrock.CfnKnowledgeBase.KnowledgeBaseConfigurationProperty(
                    type="VECTOR",
                    vector_knowledge_base_configuration=(
                        bedrock.CfnKnowledgeBase.VectorKnowledgeBaseConfigurationProperty(
                            embedding_model_arn=self._arn_modelo_embeddings(),
                        )
                    ),
                )
            ),
            storage_configuration=(
                bedrock.CfnKnowledgeBase.StorageConfigurationProperty(
                    type="OPENSEARCH_SERVERLESS",
                    opensearch_serverless_configuration=(
                        bedrock.CfnKnowledgeBase.OpenSearchServerlessConfigurationProperty(
                            collection_arn=self.colecao_arn,
                            vector_index_name=spec.nome_indice,
                            field_mapping=(
                                bedrock.CfnKnowledgeBase.OpenSearchServerlessFieldMappingProperty(
                                    vector_field=spec.campo_vetorial,
                                    text_field=spec.campo_texto,
                                    metadata_field=spec.campo_metadados,
                                )
                            ),
                        )
                    ),
                )
            ),
        )

    def _criar_data_source(self) -> bedrock.CfnDataSource:
        """Cria a data source S3 apontando para ``kb-documentos/``.

        - ``s3_configuration``: bucket ``s3-investimentos`` com prefixo de
          inclusão ``kb-documentos/`` (só esse prefixo é indexado).
        - ``vector_ingestion_configuration``: chunking de tamanho fixo com
          ``max_tokens = 512`` (~512 tokens por chunk, conforme design.md) e
          overlap de 20% entre chunks adjacentes.
        - ``data_deletion_policy = RETAIN``: preserva os vetores no índice ao
          remover a data source, evitando exclusões acidentais de dados indexados.
        """
        return bedrock.CfnDataSource(
            self,
            "DataSourceKBDocumentos",
            knowledge_base_id=self.knowledge_base.attr_knowledge_base_id,
            name=self.config.nome_recurso("maia-kb-documentos"),
            data_source_configuration=(
                bedrock.CfnDataSource.DataSourceConfigurationProperty(
                    type="S3",
                    s3_configuration=(
                        bedrock.CfnDataSource.S3DataSourceConfigurationProperty(
                            bucket_arn=self.bucket_investimentos.bucket_arn,
                            inclusion_prefixes=[PREFIXO_KB_DOCUMENTOS],
                        )
                    ),
                )
            ),
            vector_ingestion_configuration=(
                bedrock.CfnDataSource.VectorIngestionConfigurationProperty(
                    chunking_configuration=(
                        bedrock.CfnDataSource.ChunkingConfigurationProperty(
                            chunking_strategy="FIXED_SIZE",
                            fixed_size_chunking_configuration=(
                                bedrock.CfnDataSource.FixedSizeChunkingConfigurationProperty(
                                    max_tokens=MAX_TOKENS_CHUNK,
                                    overlap_percentage=OVERLAP_PERCENTUAL_CHUNK,
                                )
                            ),
                        )
                    ),
                )
            ),
            data_deletion_policy="RETAIN",
        )
