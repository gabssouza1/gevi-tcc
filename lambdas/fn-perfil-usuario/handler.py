"""Função Lambda ``fn-perfil-usuario``.

Aciona: Agente_Perfil (via AgentCore Gateway / tool routing).
Responsabilidade: CRUD do Perfil_Investidor na tabela DynamoDB ``Users``.

A função é acionada como *tool* do AgentCore Gateway. O ``event`` carrega a
operação desejada (``criar`` / ``ler`` / ``atualizar`` / ``remover``) e os dados
do perfil. A implementação separa lógica pura (parsing, validação e montagem de
respostas) da camada de I/O (``RepositorioUsuarios``, que fala com o DynamoDB),
para manter o código testável (Tarefa 5.2).

Requisitos cobertos:
- 1.1 criar conta e armazenar dados do Usuario;
- 1.2 mensagens de erro específicas por campo inválido;
- 1.3 informar quando o e-mail já está em uso;
- 1.4 coletar nível de conhecimento e tolerância a risco no cadastro;
- 3.1 disponibilizar o perfil (risco + conhecimento) para o Agente_Perfil.
"""

from __future__ import annotations

import json
import os
from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.conditions import Attr

# ``shared`` é vendorizado no artefato da Lambda (ver lambdas/README.md) e fica
# disponível na raiz do pacote implantado.
from shared import (
    ErroValidacao,
    Nivel_Conhecimento,
    Perfil_Investidor,
    Perfil_Risco,
    from_dict,
    log_erro,
    log_evento,
    obter_logger,
    to_dict,
    validar_email,
    validar_perfil_investidor,
)

logger = obter_logger(__name__)

# Nome da tabela DynamoDB via variável de ambiente (padrão alinhado ao design).
NOME_TABELA_USERS = os.environ.get("TABELA_USERS", "Users")

# Campos obrigatórios do Perfil_Investidor coletados no cadastro (Requisito 1.4).
CAMPOS_OBRIGATORIOS = ("user_id", "email", "nome", "nivel_conhecimento", "tolerancia_risco")

# Sinônimos aceitos para o nome da operação, normalizados para o verbo canônico.
_SINONIMOS_OPERACAO = {
    "criar": "criar",
    "create": "criar",
    "cadastrar": "criar",
    "ler": "ler",
    "obter": "ler",
    "consultar": "ler",
    "get": "ler",
    "read": "ler",
    "atualizar": "atualizar",
    "update": "atualizar",
    "editar": "atualizar",
    "remover": "remover",
    "excluir": "remover",
    "delete": "remover",
    "remove": "remover",
}

# Mapeamento de chaves camelCase (frontend/Cognito) → snake_case do modelo.
_ALIASES_CAMPOS = {
    "userId": "user_id",
    "nivelConhecimento": "nivel_conhecimento",
    "toleranciaRisco": "tolerancia_risco",
    "limiaresAlerta": "limiares_alerta",
}

# Códigos de erro retornados ao chamador (Gateway/agente).
COD_OPERACAO_INVALIDA = "OPERACAO_INVALIDA"
COD_DADOS_INVALIDOS = "DADOS_INVALIDOS"
COD_EMAIL_DUPLICADO = "EMAIL_DUPLICADO"
COD_NAO_ENCONTRADO = "NAO_ENCONTRADO"
COD_ERRO_INTERNO = "ERRO_INTERNO"


class PerfilDuplicadoError(Exception):
    """E-mail já registrado por outro usuário (Requisito 1.3)."""


class PerfilNaoEncontradoError(Exception):
    """Nenhum perfil encontrado para o ``user_id`` informado."""


# ---------------------------------------------------------------------------
# Lógica pura: parsing, normalização e conversão (sem I/O)
# ---------------------------------------------------------------------------
def _extrair_operacao(event: dict[str, Any]) -> str:
    """Extrai e normaliza a operação CRUD solicitada no ``event``.

    Aceita as chaves ``operacao``/``operation``/``action`` e sinônimos comuns.
    Lança ``ErroValidacao`` quando ausente ou não reconhecida.
    """
    bruto = event.get("operacao") or event.get("operation") or event.get("action")
    if not bruto or not isinstance(bruto, str):
        raise ErroValidacao(
            "A operação é obrigatória (use um de: criar, ler, atualizar, remover)."
        )
    operacao = _SINONIMOS_OPERACAO.get(bruto.strip().lower())
    if operacao is None:
        raise ErroValidacao(
            f"Operação '{bruto}' não suportada. Use: criar, ler, atualizar ou remover."
        )
    return operacao


def _extrair_dados_perfil(event: dict[str, Any]) -> dict[str, Any]:
    """Obtém o dicionário de dados do perfil a partir do ``event``.

    Aceita o payload em ``perfil``/``dados``/``body`` ou no próprio ``event``
    (chaves de topo). Normaliza chaves camelCase para o formato do modelo.
    """
    dados = event.get("perfil") or event.get("dados") or event.get("body")
    if dados is None:
        # Sem envelope: assume que os campos vieram no topo do event.
        dados = {
            chave: valor
            for chave, valor in event.items()
            if chave not in {"operacao", "operation", "action"}
        }
    if isinstance(dados, str):
        # Gateway pode entregar o corpo como string JSON.
        try:
            dados = json.loads(dados)
        except json.JSONDecodeError as exc:
            raise ErroValidacao("O corpo do perfil não é um JSON válido.") from exc
    if not isinstance(dados, dict):
        raise ErroValidacao("Os dados do perfil devem ser um objeto (dicionário).")
    return _normalizar_chaves(dados)


def _normalizar_chaves(dados: dict[str, Any]) -> dict[str, Any]:
    """Converte chaves camelCase conhecidas para o snake_case do modelo."""
    return {_ALIASES_CAMPOS.get(chave, chave): valor for chave, valor in dados.items()}


def _extrair_user_id(event: dict[str, Any]) -> str:
    """Extrai o ``user_id`` para operações de leitura/remoção.

    Procura no topo do ``event`` (``user_id``/``userId``) e, como alternativa,
    dentro do envelope ``perfil``/``dados``.
    """
    envelope = event.get("perfil") or event.get("dados") or {}
    if not isinstance(envelope, dict):
        envelope = {}
    user_id = (
        event.get("user_id")
        or event.get("userId")
        or envelope.get("user_id")
        or envelope.get("userId")
    )
    if not user_id or not isinstance(user_id, str) or not user_id.strip():
        raise ErroValidacao("O campo 'user_id' é obrigatório para esta operação.")
    return user_id


def coletar_erros_campos(dados: dict[str, Any]) -> dict[str, str]:
    """Valida os dados de entrada campo a campo (Requisito 1.2).

    Retorna um mapa ``campo → mensagem`` com todos os problemas encontrados,
    permitindo devolver mensagens específicas para cada campo inválido de uma
    só vez, em vez de parar no primeiro erro.
    """
    erros: dict[str, str] = {}

    for campo in CAMPOS_OBRIGATORIOS:
        valor = dados.get(campo)
        if valor is None or (isinstance(valor, str) and not valor.strip()):
            erros[campo] = f"O campo '{campo}' é obrigatório."

    if "email" not in erros:
        try:
            validar_email(dados["email"])
        except ErroValidacao as exc:
            erros["email"] = str(exc)

    if "nivel_conhecimento" not in erros:
        try:
            Nivel_Conhecimento(dados["nivel_conhecimento"])
        except ValueError:
            validos = ", ".join(m.value for m in Nivel_Conhecimento)
            erros["nivel_conhecimento"] = (
                f"O campo 'nivel_conhecimento' deve ser um de: {validos}."
            )

    if "tolerancia_risco" not in erros:
        try:
            Perfil_Risco(dados["tolerancia_risco"])
        except ValueError:
            validos = ", ".join(m.value for m in Perfil_Risco)
            erros["tolerancia_risco"] = (
                f"O campo 'tolerancia_risco' deve ser um de: {validos}."
            )

    if "objetivos" in dados and not isinstance(dados["objetivos"], list):
        erros["objetivos"] = "O campo 'objetivos' deve ser uma lista."

    return erros


def construir_perfil(dados: dict[str, Any]) -> Perfil_Investidor:
    """Constrói e valida um ``Perfil_Investidor`` a partir do dicionário.

    Primeiro coleta erros campo a campo (mensagens específicas); em seguida
    delega ao validador compartilhado para as regras de domínio restantes.
    Lança ``ErroValidacao`` (com atributo ``erros``) quando algo é inválido.
    """
    erros = coletar_erros_campos(dados)
    if erros:
        raise _erro_validacao_campos(erros)

    perfil = from_dict(Perfil_Investidor, dados)
    return validar_perfil_investidor(perfil)


def _erro_validacao_campos(erros: dict[str, str]) -> ErroValidacao:
    """Cria um ``ErroValidacao`` agregando as mensagens por campo."""
    resumo = "; ".join(f"{campo}: {msg}" for campo, msg in erros.items())
    exc = ErroValidacao(resumo)
    exc.erros = erros  # type: ignore[attr-defined]
    return exc


def _para_dynamo(item: dict[str, Any]) -> dict[str, Any]:
    """Serializa o item para tipos aceitos pelo DynamoDB (float → Decimal)."""
    return json.loads(json.dumps(item), parse_float=Decimal)


def _de_dynamo(valor: Any) -> Any:
    """Converte recursivamente ``Decimal`` (DynamoDB) de volta para int/float."""
    if isinstance(valor, list):
        return [_de_dynamo(item) for item in valor]
    if isinstance(valor, dict):
        return {chave: _de_dynamo(item) for chave, item in valor.items()}
    if isinstance(valor, Decimal):
        inteiro = int(valor)
        return inteiro if valor == inteiro else float(valor)
    return valor


# ---------------------------------------------------------------------------
# Respostas padronizadas devolvidas ao chamador (Gateway/agente)
# ---------------------------------------------------------------------------
def _resposta_ok(operacao: str, **conteudo: Any) -> dict[str, Any]:
    """Monta uma resposta de sucesso."""
    return {"sucesso": True, "operacao": operacao, **conteudo}


def _resposta_erro(codigo: str, mensagem: str, **extra: Any) -> dict[str, Any]:
    """Monta uma resposta de erro estruturada."""
    resposta: dict[str, Any] = {"sucesso": False, "codigo": codigo, "erro": mensagem}
    resposta.update(extra)
    return resposta


# ---------------------------------------------------------------------------
# Camada de I/O: repositório do DynamoDB (Users)
# ---------------------------------------------------------------------------
class RepositorioUsuarios:
    """Encapsula o acesso à tabela ``Users`` do DynamoDB."""

    def __init__(self, tabela: Any) -> None:
        self._tabela = tabela

    @classmethod
    def padrao(cls) -> "RepositorioUsuarios":
        """Cria o repositório usando o recurso boto3 e a tabela do ambiente."""
        recurso = boto3.resource("dynamodb")
        return cls(recurso.Table(NOME_TABELA_USERS))

    def buscar_por_id(self, user_id: str) -> Perfil_Investidor | None:
        """Lê um perfil pelo ``user_id`` (retorna ``None`` se não existir)."""
        resposta = self._tabela.get_item(Key={"userId": user_id})
        item = resposta.get("Item")
        if item is None:
            return None
        return _item_para_perfil(item)

    def email_em_uso(self, email: str, *, ignorar_user_id: str | None = None) -> bool:
        """Verifica se o e-mail já pertence a outro usuário (Requisito 1.3).

        A tabela ``Users`` tem apenas ``userId`` como chave, então a checagem de
        unicidade do e-mail usa ``scan`` com filtro (aceitável para o protótipo).
        """
        alvo = email.strip().lower()
        chave_exclusiva: dict[str, Any] | None = None
        while True:
            argumentos: dict[str, Any] = {
                "FilterExpression": Attr("email").eq(alvo),
                "ProjectionExpression": "userId",
            }
            if chave_exclusiva:
                argumentos["ExclusiveStartKey"] = chave_exclusiva
            resposta = self._tabela.scan(**argumentos)
            for item in resposta.get("Items", []):
                if item.get("userId") != ignorar_user_id:
                    return True
            chave_exclusiva = resposta.get("LastEvaluatedKey")
            if not chave_exclusiva:
                return False

    def criar(self, perfil: Perfil_Investidor) -> Perfil_Investidor:
        """Persiste um novo perfil, garantindo unicidade de e-mail e id."""
        if self.email_em_uso(perfil.email):
            raise PerfilDuplicadoError(
                f"O e-mail '{perfil.email}' já está em uso por outro usuário."
            )
        item = _perfil_para_item(perfil)
        try:
            self._tabela.put_item(
                Item=item,
                ConditionExpression=Attr("userId").not_exists(),
            )
        except self._tabela.meta.client.exceptions.ConditionalCheckFailedException as exc:
            raise PerfilDuplicadoError(
                f"Já existe um usuário com o id '{perfil.user_id}'."
            ) from exc
        return perfil

    def atualizar(self, perfil: Perfil_Investidor) -> Perfil_Investidor:
        """Atualiza um perfil existente (falha se o ``user_id`` não existir)."""
        if self.buscar_por_id(perfil.user_id) is None:
            raise PerfilNaoEncontradoError(
                f"Nenhum perfil encontrado para o id '{perfil.user_id}'."
            )
        if self.email_em_uso(perfil.email, ignorar_user_id=perfil.user_id):
            raise PerfilDuplicadoError(
                f"O e-mail '{perfil.email}' já está em uso por outro usuário."
            )
        self._tabela.put_item(Item=_perfil_para_item(perfil))
        return perfil

    def remover(self, user_id: str) -> None:
        """Remove um perfil pelo ``user_id`` (falha se não existir)."""
        try:
            self._tabela.delete_item(
                Key={"userId": user_id},
                ConditionExpression=Attr("userId").exists(),
            )
        except self._tabela.meta.client.exceptions.ConditionalCheckFailedException as exc:
            raise PerfilNaoEncontradoError(
                f"Nenhum perfil encontrado para o id '{user_id}'."
            ) from exc


def _perfil_para_item(perfil: Perfil_Investidor) -> dict[str, Any]:
    """Converte o modelo em item do DynamoDB (com ``userId`` como chave)."""
    item = _para_dynamo(to_dict(perfil))
    # A chave de partição da tabela ``Users`` é ``userId`` (camelCase).
    item["userId"] = perfil.user_id
    return item


def _item_para_perfil(item: dict[str, Any]) -> Perfil_Investidor:
    """Reconstrói o modelo a partir de um item do DynamoDB."""
    dados = _de_dynamo(item)
    dados.pop("userId", None)  # chave física redundante com ``user_id``
    return from_dict(Perfil_Investidor, dados)


# ---------------------------------------------------------------------------
# Roteamento das operações (orquestra lógica pura + repositório)
# ---------------------------------------------------------------------------
def processar(event: dict[str, Any], repositorio: RepositorioUsuarios) -> dict[str, Any]:
    """Executa a operação CRUD solicitada usando o ``repositorio`` fornecido.

    Separado do ``handler`` para permitir testes com um repositório simulado
    (ex.: DynamoDB via moto) sem depender de variáveis de ambiente globais.
    """
    try:
        operacao = _extrair_operacao(event)
    except ErroValidacao as exc:
        return _resposta_erro(COD_OPERACAO_INVALIDA, str(exc))

    try:
        if operacao == "criar":
            perfil = construir_perfil(_extrair_dados_perfil(event))
            criado = repositorio.criar(perfil)
            return _resposta_ok("criar", perfil=to_dict(criado))

        if operacao == "ler":
            user_id = _extrair_user_id(event)
            perfil = repositorio.buscar_por_id(user_id)
            if perfil is None:
                return _resposta_erro(
                    COD_NAO_ENCONTRADO,
                    f"Nenhum perfil encontrado para o id '{user_id}'.",
                )
            return _resposta_ok("ler", perfil=to_dict(perfil))

        if operacao == "atualizar":
            perfil = construir_perfil(_extrair_dados_perfil(event))
            atualizado = repositorio.atualizar(perfil)
            return _resposta_ok("atualizar", perfil=to_dict(atualizado))

        if operacao == "remover":
            user_id = _extrair_user_id(event)
            repositorio.remover(user_id)
            return _resposta_ok("remover", user_id=user_id)

    except ErroValidacao as exc:
        extra = {"campos": exc.erros} if hasattr(exc, "erros") else {}
        return _resposta_erro(COD_DADOS_INVALIDOS, str(exc), **extra)
    except PerfilDuplicadoError as exc:
        return _resposta_erro(COD_EMAIL_DUPLICADO, str(exc))
    except PerfilNaoEncontradoError as exc:
        return _resposta_erro(COD_NAO_ENCONTRADO, str(exc))

    # Inalcançável: _extrair_operacao só retorna verbos suportados.
    return _resposta_erro(COD_OPERACAO_INVALIDA, f"Operação '{operacao}' não tratada.")


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Ponto de entrada da Lambda acionada pelo Agente_Perfil via Gateway."""
    if not isinstance(event, dict):
        return _resposta_erro(COD_DADOS_INVALIDOS, "O event deve ser um objeto JSON.")
    log_evento(logger, "invocacao_recebida", context=context)
    try:
        repositorio = RepositorioUsuarios.padrao()
        resposta = processar(event, repositorio)
        log_evento(
            logger,
            "invocacao_concluida",
            context=context,
            operacao=resposta.get("operacao"),
            sucesso=resposta.get("sucesso"),
        )
        return resposta
    except Exception as exc:  # noqa: BLE001 — fronteira: nunca vazar exceção crua
        log_erro(logger, "erro_interno", context=context, erro=str(exc))
        return _resposta_erro(COD_ERRO_INTERNO, f"Erro interno ao processar o perfil: {exc}")
