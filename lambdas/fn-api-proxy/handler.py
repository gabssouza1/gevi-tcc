"""Proxy HTTP entre o frontend estático e o Amazon Bedrock AgentCore.

Contexto (design.md — opção "b" de integração): o frontend é um export estático
(S3 + CloudFront) e autentica no Cognito, obtendo um JWT. Nem o AgentCore Gateway
nem o Runtime aceitam requisições cross-origin de um navegador (sem CORS) e o
Runtime, com *inbound auth* por JWT, precisa ser chamado por uma requisição HTTPS
com ``Authorization: Bearer <token>`` (a AWS documenta que o SDK/SigV4 não é usado
nesse modo). Esta Lambda fica atrás de um HTTP API (autorizado pelo mesmo Cognito)
e repassa o *access token* do usuário para o AgentCore:

- ``POST /chat``            → invoca o Runtime dos 5 agentes (pipeline completo).
- ``POST /gateway/{tool}``  → chama uma tool do Gateway (MCP/JSON-RPC): dados
  reais (indicadores, portfólio, histórico, perfil, simulação).

Princípio de segurança: o ``user_id`` NUNCA vem do corpo enviado pelo cliente —
é sempre o ``sub`` do JWT já validado pelo authorizer do API Gateway. Assim um
usuário não consegue consultar dados de outro. O CORS é tratado pelo próprio
HTTP API (configuração da stack), não aqui.

Usa apenas a biblioteca padrão (urllib/json/os/uuid): sem dependências externas.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any


def _hora() -> str:
    """Hora local (Brasil, UTC-3) em HH:MM para exibir nas mensagens."""
    return datetime.now(timezone(timedelta(hours=-3))).strftime("%H:%M")


def _iso() -> str:
    """Timestamp ISO-8601 (UTC) para ordenação das conversas."""
    return datetime.now(timezone.utc).isoformat()

GATEWAY_URL = os.getenv("GATEWAY_URL", "")
RUNTIME_ARN = os.getenv("RUNTIME_ARN", "")
RUNTIME_QUALIFIER = os.getenv("RUNTIME_QUALIFIER", "")
REGIAO = os.getenv("REGIAO", os.getenv("AWS_REGION", "us-east-1"))
JOBS_TABLE = os.getenv("JOBS_TABLE", "dev-ChatJobs")
CONVERSAS_TABLE = os.getenv("CONVERSAS_TABLE", "dev-ChatConversas")
PORTFOLIOS_TABLE = os.getenv("PORTFOLIOS_TABLE", "dev-Portfolios")
USERS_TABLE = os.getenv("USERS_TABLE", "dev-Users")

# Tipos de ativo aceitos na carteira (mesmo enum lido por fn-analise-portfolio).
_TIPOS_ATIVO = {"RENDA_FIXA", "RENDA_VARIAVEL", "FII", "CRIPTO"}
# Indicadores que aceitam limiar de alerta configurável.
_INDICADORES_ALERTA = {"SELIC", "IPCA", "DOLAR", "CDI"}
# Limite de exposição a ativos de maior risco por perfil (mesma régua da análise
# de portfólio) e classes consideradas de risco alto.
_LIMITE_RISCO_PERFIL = {"CONSERVADOR": 20.0, "MODERADO": 50.0, "ARROJADO": 100.0}
_TIPO_ALTO_RISCO = {"RENDA_VARIAVEL", "CRIPTO"}
_LIMIAR_CONCENTRACAO = 30.0

# Timeouts de egress (s). O chat (job assíncrono) roda no worker com timeout
# alto; as tools e o avaliar_perfil (síncronos) são rápidos.
_TIMEOUT_CHAT = 240
_TIMEOUT_TOOL = 25
# TTL dos jobs (segundos) — limpeza automática pelo DynamoDB.
_TTL_JOB = 3600

# Clientes boto3 (disponíveis no runtime da Lambda), criados sob demanda.
_ddb_tabela = None
_conversas_tabela = None
_portfolios_tabela = None
_users_tabela = None
_lambda_cli = None


def _tabela_jobs():
    global _ddb_tabela
    if _ddb_tabela is None:
        import boto3

        _ddb_tabela = boto3.resource("dynamodb", region_name=REGIAO).Table(JOBS_TABLE)
    return _ddb_tabela


def _tabela_conversas():
    global _conversas_tabela
    if _conversas_tabela is None:
        import boto3

        _conversas_tabela = boto3.resource("dynamodb", region_name=REGIAO).Table(
            CONVERSAS_TABLE
        )
    return _conversas_tabela


def _tabela_portfolios():
    global _portfolios_tabela
    if _portfolios_tabela is None:
        import boto3

        _portfolios_tabela = boto3.resource("dynamodb", region_name=REGIAO).Table(
            PORTFOLIOS_TABLE
        )
    return _portfolios_tabela


def _tabela_users():
    global _users_tabela
    if _users_tabela is None:
        import boto3

        _users_tabela = boto3.resource("dynamodb", region_name=REGIAO).Table(USERS_TABLE)
    return _users_tabela


def _invocar_async(nome_funcao: str, payload: dict[str, Any]) -> None:
    """Auto-invoca a Lambda de forma assíncrona (worker do job de chat)."""
    global _lambda_cli
    if _lambda_cli is None:
        import boto3

        _lambda_cli = boto3.client("lambda", region_name=REGIAO)
    _lambda_cli.invoke(
        FunctionName=nome_funcao,
        InvocationType="Event",
        Payload=json.dumps(payload).encode("utf-8"),
    )


def _resposta(codigo: int, corpo: dict[str, Any]) -> dict[str, Any]:
    """Monta a resposta no formato do HTTP API (payload v2)."""
    return {
        "statusCode": codigo,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(corpo),
    }


def _bearer(headers: dict[str, str]) -> str | None:
    """Extrai o token do header Authorization, garantindo o prefixo ``Bearer``."""
    # No HTTP API as chaves de header chegam em minúsculas.
    bruto = headers.get("authorization") or headers.get("Authorization")
    if not bruto:
        return None
    return bruto if bruto.lower().startswith("bearer ") else f"Bearer {bruto}"


def _user_id_do_jwt(evento: dict[str, Any]) -> str | None:
    """Lê o ``sub`` das claims do JWT já validado pelo authorizer do API Gateway."""
    try:
        claims = evento["requestContext"]["authorizer"]["jwt"]["claims"]
    except (KeyError, TypeError):
        return None
    return claims.get("sub") or claims.get("username")


def _corpo_json(evento: dict[str, Any]) -> dict[str, Any]:
    """Decodifica o corpo JSON da requisição (tolerante a corpo vazio)."""
    corpo = evento.get("body") or "{}"
    if evento.get("isBase64Encoded"):
        import base64

        corpo = base64.b64decode(corpo).decode("utf-8")
    try:
        dados = json.loads(corpo)
    except (ValueError, TypeError):
        return {}
    return dados if isinstance(dados, dict) else {}


def _post_json(
    url: str, corpo: dict[str, Any], token: str, *, timeout: int, extra: dict | None = None
) -> tuple[int, Any]:
    """POST JSON com Bearer; retorna ``(status, corpo_decodificado_ou_texto)``."""
    dados = json.dumps(corpo).encode("utf-8")
    cabecalhos = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": token,
    }
    if extra:
        cabecalhos.update(extra)
    req = urllib.request.Request(url, data=dados, headers=cabecalhos, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            texto = resp.read().decode("utf-8")
            status = resp.status
    except urllib.error.HTTPError as exc:
        texto = exc.read().decode("utf-8", "replace")
        status = exc.code
    try:
        return status, json.loads(texto)
    except (ValueError, TypeError):
        return status, texto


def _invocar_runtime(payload: dict[str, Any], token: str, user_id: str) -> tuple[int, Any]:
    """Invoca o Runtime dos agentes por HTTPS (inbound auth por JWT).

    URL de dados do InvokeAgentRuntime: o ARN do runtime é URL-encoded no path e
    o endpoint (qualifier) vai na query. O header de sessão do Runtime exige no
    mínimo 33 caracteres.
    """
    if not RUNTIME_ARN:
        return 503, {"erro": "Runtime dos agentes indisponivel (nao provisionado)."}

    arn_encoded = urllib.parse.quote(RUNTIME_ARN, safe="")
    url = (
        f"https://bedrock-agentcore.{REGIAO}.amazonaws.com/runtimes/"
        f"{arn_encoded}/invocations"
    )
    if RUNTIME_QUALIFIER:
        url += f"?qualifier={urllib.parse.quote(RUNTIME_QUALIFIER, safe='')}"

    sessao = f"{user_id}-{uuid.uuid4().hex}"
    if len(sessao) < 33:
        sessao = (sessao + uuid.uuid4().hex)[:64]
    extra = {"X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": sessao}
    return _post_json(url, payload, token, timeout=_TIMEOUT_CHAT, extra=extra)


# Mapa {nome_logico -> nome_real} das tools do Gateway. O AgentCore registra as
# tools como ``<target>___<tool>``; resolvemos via tools/list (cacheado) para não
# depender do prefixo/ambiente — mesmo contrato do servidor.py do Runtime.
_MAPA_TOOLS_GATEWAY: dict[str, str] | None = None


def _extrair_json_mcp(bruto: str) -> Any:
    """Decodifica a resposta MCP, aceitando JSON puro ou event-stream (SSE)."""
    texto = (bruto or "").strip()
    try:
        return json.loads(texto)
    except (ValueError, TypeError):
        pass
    for linha in reversed(texto.splitlines()):
        linha = linha.strip()
        if linha.startswith("data:"):
            try:
                return json.loads(linha[len("data:"):].strip())
            except (ValueError, TypeError):
                continue
    return {"raw": bruto}


def _desembrulhar_mcp(resultado: Any) -> Any:
    """Extrai o payload real de um resultado MCP ``{content:[{text:'<json>'}]}``."""
    if isinstance(resultado, dict) and isinstance(resultado.get("content"), list):
        for bloco in resultado["content"]:
            if isinstance(bloco, dict) and bloco.get("text"):
                try:
                    return json.loads(bloco["text"])
                except (ValueError, TypeError):
                    return {"texto": bloco["text"]}
    return resultado


def _post_gateway(corpo: dict[str, Any], token: str) -> Any:
    """POST JSON-RPC ao Gateway (MCP Streamable HTTP); retorna o payload decodificado.

    O Gateway pode responder em JSON puro ou em event-stream (SSE); por isso o
    ``Accept`` inclui ambos e o corpo é decodificado por ``_extrair_json_mcp``.
    """
    dados = json.dumps(corpo).encode("utf-8")
    cabecalhos = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Authorization": token,
    }
    req = urllib.request.Request(GATEWAY_URL, data=dados, headers=cabecalhos, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_TOOL) as resp:  # noqa: S310
            return _extrair_json_mcp(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return _extrair_json_mcp(exc.read().decode("utf-8", "replace"))


def _carregar_mapa_tools(token: str) -> dict[str, str]:
    """Carrega e cacheia o mapa de nomes de tools do Gateway (via tools/list)."""
    global _MAPA_TOOLS_GATEWAY
    if _MAPA_TOOLS_GATEWAY is not None:
        return _MAPA_TOOLS_GATEWAY
    mapa: dict[str, str] = {}
    payload = _post_gateway(
        {"jsonrpc": "2.0", "id": "list", "method": "tools/list", "params": {}}, token
    )
    tools = payload.get("result", {}).get("tools", []) if isinstance(payload, dict) else []
    for t in tools:
        nome = t.get("name", "")
        sufixo = nome.split("___")[-1] if "___" in nome else nome
        if sufixo:
            mapa[sufixo] = nome
    if mapa:
        _MAPA_TOOLS_GATEWAY = mapa
    return mapa


def _chamar_tool_gateway(
    tool: str, argumentos: dict[str, Any], token: str
) -> tuple[int, Any]:
    """Chama uma tool do Gateway via MCP/JSON-RPC (mesmo contrato do servidor.py).

    Resolve o nome lógico da tool para o nome real registrado no Gateway
    (``<target>___<tool>``) e desembrulha o resultado MCP (``content[].text``).
    """
    if not GATEWAY_URL:
        return 503, {"erro": "Gateway indisponivel (nao configurado)."}
    nome_real = _carregar_mapa_tools(token).get(tool, tool)
    payload = _post_gateway(
        {
            "jsonrpc": "2.0",
            "id": nome_real,
            "method": "tools/call",
            "params": {"name": nome_real, "arguments": argumentos},
        },
        token,
    )
    if isinstance(payload, dict) and "error" in payload:
        return 502, payload["error"]
    resultado = payload.get("result", payload) if isinstance(payload, dict) else payload
    return 200, _desembrulhar_mcp(resultado)


def _iniciar_job_chat(
    payload: dict[str, Any],
    token: str,
    user_id: str,
    nome_funcao: str,
    conversa_id: str | None = None,
) -> dict[str, Any]:
    """Cria um job pendente e dispara o worker assíncrono; devolve o jobId."""
    import time

    job_id = uuid.uuid4().hex
    _tabela_jobs().put_item(
        Item={
            "jobId": job_id,
            "userId": user_id,
            "status": "pending",
            "ttl": int(time.time()) + _TTL_JOB,
        }
    )
    _invocar_async(
        nome_funcao,
        {
            "_worker": True,
            "jobId": job_id,
            "token": token,
            "payload": payload,
            "user_id": user_id,
            "conversaId": conversa_id,
        },
    )
    return {"jobId": job_id, "status": "pending"}


def _status_job(job_id: str, user_id: str) -> dict[str, Any]:
    """Consulta o resultado de um job (valida o dono pelo user_id do JWT)."""
    if not job_id:
        return _resposta(400, {"erro": "jobId nao informado."})
    item = _tabela_jobs().get_item(Key={"jobId": job_id}).get("Item")
    if not item:
        return _resposta(404, {"status": "unknown"})
    if item.get("userId") != user_id:
        return _resposta(403, {"erro": "Acesso negado ao job."})
    estado = item.get("status")
    if estado == "done":
        return _resposta(200, {"status": "done", "resultado": json.loads(item["resultado"])})
    if estado == "error":
        return _resposta(200, {"status": "error", "erro": json.loads(item.get("erro") or '""')})
    # "etapa": descrição em português da fase atual do pipeline (gravada pelo
    # Runtime direto na tabela via _log_progresso), ex.: "Analisando sua
    # carteira e selecionando ativos" — feedback incremental durante o
    # polling, sem streaming real (ver docs/decisoes-agentes-e-latencia.md).
    resposta: dict[str, Any] = {"status": "pending"}
    if item.get("etapa"):
        resposta["etapa"] = item["etapa"]
    return _resposta(200, resposta)


def _processar_worker(evento: dict[str, Any]) -> dict[str, Any]:
    """Execução assíncrona (worker): chama o Runtime e grava o resultado no job."""
    job_id = evento["jobId"]
    try:
        # jobId no payload: o Runtime usa para gravar o progresso (etapa
        # atual do pipeline) direto na tabela de jobs, já que a chamada ao
        # Runtime é uma única requisição HTTP síncrona sem canal de volta
        # durante a execução (ver docs/decisoes-agentes-e-latencia.md).
        payload_runtime = dict(evento["payload"])
        payload_runtime["jobId"] = job_id
        status, resultado = _invocar_runtime(
            payload_runtime, evento["token"], evento["user_id"]
        )
        campo = "resultado" if status < 400 else "erro"
        estado = "done" if status < 400 else "error"
        _tabela_jobs().update_item(
            Key={"jobId": job_id},
            UpdateExpression="SET #s = :s, #c = :c",
            ExpressionAttributeNames={"#s": "status", "#c": campo},
            ExpressionAttributeValues={":s": estado, ":c": json.dumps(resultado)},
        )
        # Persiste a resposta da IA no histórico da conversa (se houver).
        conversa_id = evento.get("conversaId")
        texto_ia = resultado.get("resposta") if isinstance(resultado, dict) else None
        if conversa_id and status < 400 and texto_ia:
            try:
                _append_mensagem(
                    evento["user_id"],
                    conversa_id,
                    {"autor": "ia", "texto": texto_ia, "hora": _hora()},
                )
            except Exception:  # noqa: BLE001 - persistência não derruba o job
                pass
    except Exception as exc:  # noqa: BLE001
        _tabela_jobs().update_item(
            Key={"jobId": job_id},
            UpdateExpression="SET #s = :s, erro = :e",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": "error", ":e": json.dumps(str(exc))},
        )
    return {"ok": True, "jobId": job_id}


# ---------------------------------------------------------------------------
# Persistência do histórico de conversas do chat (dev-ChatConversas).
# ---------------------------------------------------------------------------
def _titulo_de(mensagem: str) -> str:
    """Deriva um título curto a partir da primeira mensagem do usuário."""
    texto = (mensagem or "").strip().replace("\n", " ")
    if not texto:
        return "Nova conversa"
    return texto[:48] + ("…" if len(texto) > 48 else "")


def _criar_conversa(user_id: str, primeira_msg: str) -> str:
    """Cria uma conversa vazia e retorna o seu id."""
    conversa_id = uuid.uuid4().hex
    agora = _iso()
    _tabela_conversas().put_item(
        Item={
            "userId": user_id,
            "conversaId": conversa_id,
            "titulo": _titulo_de(primeira_msg),
            "criadaEm": agora,
            "atualizadaEm": agora,
            "mensagens": [],
        }
    )
    return conversa_id


def _append_mensagem(user_id: str, conversa_id: str, mensagem: dict[str, Any]) -> None:
    """Anexa uma mensagem à conversa e atualiza o timestamp.

    Exige que a conversa já exista (attribute_exists): sem essa condição, um
    update_item para uma chave inexistente cria um item novo do zero — o que
    ressuscitava conversas excluídas quando o worker assíncrono terminava de
    processar uma mensagem depois do usuário já ter apagado a conversa. Se a
    conversa não existe mais, a mensagem é descartada silenciosamente (o
    ConditionalCheckFailedException é esperado e não é um erro real).
    """
    from botocore.exceptions import ClientError

    try:
        _tabela_conversas().update_item(
            Key={"userId": user_id, "conversaId": conversa_id},
            UpdateExpression=(
                "SET mensagens = list_append(if_not_exists(mensagens, :v), :m), "
                "atualizadaEm = :t"
            ),
            ConditionExpression="attribute_exists(userId)",
            ExpressionAttributeValues={":m": [mensagem], ":v": [], ":t": _iso()},
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
            raise


def _listar_conversas(user_id: str) -> dict[str, Any]:
    """Lista as conversas do usuário (mais recentes primeiro)."""
    from boto3.dynamodb.conditions import Key

    itens = (
        _tabela_conversas()
        .query(KeyConditionExpression=Key("userId").eq(user_id))
        .get("Items", [])
    )
    itens.sort(key=lambda x: x.get("atualizadaEm", ""), reverse=True)
    conversas = [
        {
            "conversaId": i["conversaId"],
            "titulo": i.get("titulo", "Conversa"),
            "atualizadaEm": i.get("atualizadaEm", ""),
        }
        for i in itens
    ]
    return _resposta(200, {"conversas": conversas})


def _obter_conversa(conversa_id: str, user_id: str) -> dict[str, Any]:
    """Retorna as mensagens de uma conversa (a PK userId garante o dono)."""
    if not conversa_id:
        return _resposta(400, {"erro": "conversaId nao informado."})
    item = (
        _tabela_conversas()
        .get_item(Key={"userId": user_id, "conversaId": conversa_id})
        .get("Item")
    )
    if not item:
        return _resposta(404, {"erro": "Conversa nao encontrada."})
    return _resposta(
        200,
        {
            "conversaId": conversa_id,
            "titulo": item.get("titulo", ""),
            "mensagens": item.get("mensagens", []),
        },
    )


def _excluir_conversa(conversa_id: str, user_id: str) -> dict[str, Any]:
    """Exclui uma conversa do usuário (a PK userId garante o dono)."""
    if not conversa_id:
        return _resposta(400, {"erro": "conversaId nao informado."})
    try:
        _tabela_conversas().delete_item(
            Key={"userId": user_id, "conversaId": conversa_id}
        )
    except Exception as exc:  # noqa: BLE001
        return _resposta(502, {"erro": f"Nao foi possivel excluir a conversa: {exc}"})
    return _resposta(200, {"ok": True})


# ---------------------------------------------------------------------------
# Carteira do investidor (dev-Portfolios) — cadastro manual. A tool
# analisar_portfolio (Gateway) LÊ desta mesma tabela; aqui o usuário gerencia
# os ativos. Cada ativo é um item {userId(PK), assetId(SK), tipo_ativo, valor,
# percentual}. O percentual é derivado do valor (participação na carteira).
# ---------------------------------------------------------------------------
def _num(v: Any) -> float:
    """Converte números do DynamoDB/JSON (Decimal, str, int) em float seguro."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


# Chave reservada para o item de metadados da carteira (nunca é um assetId de
# usuário real, pois o formulário exige um nome não vazio). Guarda apenas a
# descrição textual da última alteração — não é um histórico completo (list),
# só o "diff" do salvamento mais recente, para não precisar de uma tabela
# nova (decisão: manter simples, sem mudar a arquitetura).
_ASSET_ID_META = "__meta__"


def _descrever_alteracao_portfolio(
    antigos: dict[str, dict[str, Any]], novos: dict[str, dict[str, Any]]
) -> str:
    """Resume em texto curto a diferença entre a carteira antiga e a nova.

    Detecta ativos adicionados, removidos e com valor alterado (tolerância de
    1 centavo para evitar ruído de arredondamento). Combina até 3 mudanças em
    uma frase; se houver mais, resume a quantidade.
    """
    mudancas: list[str] = []
    for asset_id, novo in novos.items():
        antigo = antigos.get(asset_id)
        if antigo is None:
            mudancas.append(f"adicionou {asset_id} (R$ {_moeda(novo['valor'])})")
        elif abs(_num(antigo.get("valor")) - _num(novo["valor"])) >= 0.01:
            mudancas.append(
                f"alterou {asset_id} (R$ {_moeda(antigo.get('valor'))} → "
                f"R$ {_moeda(novo['valor'])})"
            )
    for asset_id in antigos:
        if asset_id not in novos:
            mudancas.append(f"removeu {asset_id}")

    if not mudancas:
        return "Sem alterações na composição."
    if len(mudancas) > 3:
        mudancas = mudancas[:3] + [f"e mais {len(mudancas) - 3} alteração(ões)"]
    # Uma mudança por linha (em vez de "; " tudo numa frase só) — mais legível
    # no card do dashboard, que é estreito.
    return "\n".join(m[0].upper() + m[1:] for m in mudancas)


def _moeda(v: Any) -> str:
    """Formata um valor numérico como moeda BR simples (sem o prefixo R$)."""
    return f"{_num(v):,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def _listar_portfolio(user_id: str) -> dict[str, Any]:
    """Lista os ativos da carteira do usuário (mais valiosos primeiro)."""
    from boto3.dynamodb.conditions import Key

    itens = (
        _tabela_portfolios()
        .query(KeyConditionExpression=Key("userId").eq(user_id))
        .get("Items", [])
    )
    meta = next((i for i in itens if i.get("assetId") == _ASSET_ID_META), None)
    itens = [i for i in itens if i.get("assetId") != _ASSET_ID_META]
    ativos = [
        {
            "assetId": i.get("assetId", ""),
            "tipo_ativo": i.get("tipo_ativo", ""),
            "valor": _num(i.get("valor")),
            "percentual": _num(i.get("percentual")),
        }
        for i in itens
    ]
    ativos.sort(key=lambda a: a["valor"], reverse=True)
    total = sum(a["valor"] for a in ativos)
    # Última alteração da carteira: o "atualizadaEm" mais recente entre os
    # itens (itens antigos, salvos antes deste campo existir, não têm o
    # atributo — tratados como string vazia e ignorados no max()).
    # "ultimaAlteracaoDescricao": o "diff" textual do salvamento mais recente
    # (vem do item __meta__; sem histórico completo — ver _ASSET_ID_META).
    atualizada_em = max((i.get("atualizadaEm", "") for i in itens), default="")
    if meta and meta.get("atualizadaEm", "") > atualizada_em:
        atualizada_em = meta["atualizadaEm"]
    return _resposta(
        200,
        {
            "ativos": ativos,
            "valor_total": total,
            "atualizadaEm": atualizada_em,
            "ultimaAlteracaoDescricao": (meta or {}).get("descricao", ""),
        },
    )


def _salvar_portfolio(user_id: str, ativos_entrada: Any) -> dict[str, Any]:
    """Substitui a carteira do usuário pelos ativos informados (replace-all).

    Valida ``tipo_ativo`` e ``valor``, calcula o ``percentual`` de cada ativo a
    partir da participação no total, apaga os itens atuais do usuário e grava os
    novos. Consolida entradas repetidas (mesmo assetId + tipo) somando o valor.
    """
    from decimal import Decimal

    from boto3.dynamodb.conditions import Key

    if not isinstance(ativos_entrada, list):
        return _resposta(400, {"erro": "Campo 'ativos' deve ser uma lista."})

    # Normaliza e valida a entrada; consolida duplicatas (assetId + tipo_ativo).
    consolidado: dict[tuple[str, str], float] = {}
    for a in ativos_entrada:
        if not isinstance(a, dict):
            continue
        asset_id = str(a.get("assetId") or a.get("asset_id") or "").strip()
        tipo = str(a.get("tipo_ativo") or "").strip().upper()
        valor = _num(a.get("valor"))
        if not asset_id or tipo not in _TIPOS_ATIVO or valor <= 0:
            return _resposta(
                400,
                {
                    "erro": (
                        "Cada ativo precisa de assetId, tipo_ativo "
                        f"({', '.join(sorted(_TIPOS_ATIVO))}) e valor > 0."
                    )
                },
            )
        consolidado[(asset_id, tipo)] = consolidado.get((asset_id, tipo), 0.0) + valor

    total = sum(consolidado.values())
    tabela = _tabela_portfolios()

    # Lê a carteira ATUAL (antes de sobrescrever) para derivar a descrição da
    # alteração — precisa vir antes do batch_writer apagar/substituir os itens.
    existentes = (
        tabela.query(KeyConditionExpression=Key("userId").eq(user_id)).get("Items", [])
    )
    antigos_por_id = {
        i["assetId"]: i for i in existentes if i.get("assetId") != _ASSET_ID_META
    }

    # Monta os itens novos (chaveados por assetId; put_item sobrescreve os
    # existentes com a mesma chave). "atualizadaEm" (por item) permite mostrar
    # a data da última alteração da carteira sem precisar de uma tabela de
    # histórico dedicada (decisão: manter simples, sem mudar a arquitetura).
    agora = _iso()
    itens_novos: dict[str, dict[str, Any]] = {}
    for (asset_id, tipo), valor in consolidado.items():
        pct = (valor / total * 100.0) if total > 0 else 0.0
        itens_novos[asset_id] = {
            "userId": user_id,
            "assetId": asset_id,
            "tipo_ativo": tipo,
            "valor": Decimal(str(round(valor, 2))),
            "percentual": Decimal(str(round(pct, 2))),
            "atualizadaEm": agora,
        }

    # Item de metadados (não é um ativo real — ver _ASSET_ID_META): guarda só
    # a descrição da MUDANÇA MAIS RECENTE, não uma lista de eventos.
    descricao = _descrever_alteracao_portfolio(antigos_por_id, itens_novos)
    itens_novos[_ASSET_ID_META] = {
        "userId": user_id,
        "assetId": _ASSET_ID_META,
        "atualizadaEm": agora,
        "descricao": descricao,
    }

    # Só remove os ativos que não estão mais na carteira; os demais são
    # sobrescritos pelo put_item. Assim delete e put nunca colidem na mesma
    # chave dentro do lote (BatchWriteItem rejeita chaves duplicadas).
    ids_remover = {i["assetId"] for i in existentes} - set(itens_novos)

    with tabela.batch_writer() as lote:
        for asset_id in ids_remover:
            lote.delete_item(Key={"userId": user_id, "assetId": asset_id})
        for item in itens_novos.values():
            lote.put_item(Item=item)
    return _listar_portfolio(user_id)


# ---------------------------------------------------------------------------
# Série histórica dos indicadores (BCB/SGS) para o gráfico de evolução do
# dashboard. Busca ~12 meses por indicador e agrega ao último valor de cada mês.
# Cacheado em memória (Lambda quente) por algumas horas — os dados são mensais.
# ---------------------------------------------------------------------------
# indicador -> (código SGS, tipo de tratamento)
_SERIES_GRAFICO: dict[str, tuple[int, str]] = {
    "Selic": (432, "bruto"),   # meta Selic (% a.a.), diária -> último do mês
    "IPCA": (433, "bruto"),    # variação mensal (%)
    "CDI": (12, "cdi"),        # % a.d. (diária) -> anualiza cada ponto
    "Dólar": (1, "bruto"),     # PTAX venda (diária) -> último do mês
}
_MESES_GRAFICO = 12
_TTL_SERIE = 6 * 3600  # 6h
_cache_serie: dict[str, Any] = {"quando": 0.0, "dados": None}


def _sgs_range(codigo: int, meses: int) -> list[dict[str, Any]]:
    """Busca uma série do SGS/BCB num intervalo (últimos ``meses`` meses)."""
    fim = datetime.now(timezone.utc)
    ini = fim - timedelta(days=32 * (meses + 1))
    url = (
        f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo}/dados"
        f"?formato=json&dataInicial={ini.strftime('%d/%m/%Y')}"
        f"&dataFinal={fim.strftime('%d/%m/%Y')}"
    )
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=8) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


def _agregar_mensal(obs: list[dict[str, Any]], tipo: str) -> list[dict[str, Any]]:
    """Agrega observações do SGS ao último valor de cada mês (últimos N meses)."""
    por_mes: dict[str, tuple[str, float]] = {}
    ordem: list[str] = []
    for o in obs or []:
        data = o.get("data")
        bruto = o.get("valor")
        if not data or bruto is None:
            continue
        try:
            dia, mes, ano = str(data).split("/")
            valor = float(str(bruto).replace(",", "."))
        except (ValueError, TypeError):
            continue
        chave = f"{ano}-{mes}"
        if chave not in por_mes:
            ordem.append(chave)
        rotulo = f"{mes}/{ano[2:]}"
        por_mes[chave] = (rotulo, valor)
    pontos = [
        {"rotulo": por_mes[k][0], "valor": por_mes[k][1]}
        for k in ordem[-_MESES_GRAFICO:]
    ]
    if tipo == "cdi":  # taxa ao dia -> efetiva ao ano (252 dias úteis)
        for p in pontos:
            p["valor"] = round(((1 + p["valor"] / 100) ** 252 - 1) * 100, 2)
    else:
        for p in pontos:
            p["valor"] = round(p["valor"], 4)
    return pontos


def _serie_historica() -> dict[str, Any]:
    """Monta as séries mensais do gráfico (com cache em memória)."""
    import time

    agora = time.time()
    if (
        _cache_serie["dados"] is not None
        and (agora - _cache_serie["quando"]) < _TTL_SERIE
    ):
        return _cache_serie["dados"]
    series: dict[str, Any] = {}
    for nome, (codigo, tipo) in _SERIES_GRAFICO.items():
        try:
            series[nome] = _agregar_mensal(_sgs_range(codigo, _MESES_GRAFICO), tipo)
        except Exception:  # noqa: BLE001 - indicador sem dados não derruba o resto
            series[nome] = []
    if any(series.values()):
        _cache_serie["dados"] = series
        _cache_serie["quando"] = agora
    return series


# ---------------------------------------------------------------------------
# Notificações do investidor. Combina:
#  - Alertas de mercado (armazenados em Users.notificacoes pelo pipeline
#    fn-consulta-APIs quando |variação| > limiar configurado pelo usuário).
#  - Alertas PROATIVOS derivados da carteira (concentração, exposição de risco
#    acima do perfil, ausência de carteira, baixa diversificação), calculados a
#    cada leitura a partir de Portfolios + perfil.
# ---------------------------------------------------------------------------
def _ler_user(user_id: str) -> dict[str, Any]:
    """Lê o item do usuário (perfil, limiares e notificações) na tabela Users."""
    try:
        return _tabela_users().get_item(Key={"userId": user_id}).get("Item") or {}
    except Exception:  # noqa: BLE001 - ausência do item não deve derrubar a resposta
        return {}


def _alertas_proativos(user_id: str, item_user: dict[str, Any]) -> list[dict[str, Any]]:
    """Gera alertas proativos com base na carteira e no perfil do usuário."""
    from boto3.dynamodb.conditions import Key

    agora = _iso()
    try:
        ativos = (
            _tabela_portfolios()
            .query(KeyConditionExpression=Key("userId").eq(user_id))
            .get("Items", [])
        )
    except Exception:  # noqa: BLE001
        ativos = []

    if not ativos:
        return [
            {
                "id": "prov-sem-carteira",
                "titulo": "Cadastre sua carteira",
                "mensagem": (
                    "Você ainda não cadastrou ativos. Cadastre sua carteira para "
                    "receber análises e recomendações personalizadas."
                ),
                "timestamp": agora,
                "lida": False,
            }
        ]

    total = sum(_num(a.get("valor")) for a in ativos)

    def _pct(a: dict[str, Any]) -> float:
        p = _num(a.get("percentual"))
        if p:
            return p
        return (_num(a.get("valor")) / total * 100.0) if total > 0 else 0.0

    alertas: list[dict[str, Any]] = []
    # Concentração excessiva por ativo (> 30%).
    for a in ativos:
        p = _pct(a)
        if p > _LIMIAR_CONCENTRACAO:
            alertas.append(
                {
                    "id": f"prov-conc-{a.get('assetId')}",
                    "titulo": "Concentração elevada",
                    "mensagem": (
                        f"O ativo {a.get('assetId')} representa {p:.0f}% da sua "
                        "carteira, acima do limite recomendado de 30%. Considere "
                        "diversificar."
                    ),
                    "timestamp": agora,
                    "lida": False,
                }
            )
    # Exposição a ativos de maior risco acima do adequado ao perfil.
    perfil = str(item_user.get("tolerancia_risco", "")).upper()
    limite = _LIMITE_RISCO_PERFIL.get(perfil)
    if limite is not None:
        alto = sum(
            _pct(a)
            for a in ativos
            if str(a.get("tipo_ativo", "")).upper() in _TIPO_ALTO_RISCO
        )
        if alto > limite:
            alertas.append(
                {
                    "id": "prov-risco",
                    "titulo": "Exposição acima do seu perfil",
                    "mensagem": (
                        f"Sua carteira tem {alto:.0f}% em ativos de maior risco "
                        f"(renda variável/cripto), acima do adequado para o perfil "
                        f"{perfil.title()} (limite de ~{limite:.0f}%)."
                    ),
                    "timestamp": agora,
                    "lida": False,
                }
            )
    # Baixa diversificação: uma única classe de ativo.
    tipos = {str(a.get("tipo_ativo", "")).upper() for a in ativos if a.get("tipo_ativo")}
    if len(tipos) == 1:
        alertas.append(
            {
                "id": "prov-diversif",
                "titulo": "Baixa diversificação",
                "mensagem": (
                    "Todos os seus ativos são da mesma classe. Diversificar entre "
                    "classes de ativo pode reduzir o risco da carteira."
                ),
                "timestamp": agora,
                "lida": False,
            }
        )
    return alertas


def _listar_notificacoes(user_id: str) -> dict[str, Any]:
    """Lista notificações (alertas de mercado armazenados + proativos da carteira).

    Filtra os IDs que o usuário já descartou em ``notificacoes_descartadas``
    (via ``_limpar_notificacoes``) — sem esse filtro, os alertas proativos
    (regenerados a cada listagem por ``_alertas_proativos``) reapareciam
    mesmo depois de o usuário clicar em "Limpar".
    """
    item = _ler_user(user_id)
    descartadas = set(str(x) for x in (item.get("notificacoes_descartadas", []) or []))
    lista: list[dict[str, Any]] = []
    for n in item.get("notificacoes", []) or []:
        nid = str(n.get("id", ""))
        if nid in descartadas:
            continue
        lista.append(
            {
                "id": nid,
                "titulo": "Alerta de mercado",
                "mensagem": n.get("mensagem", ""),
                "timestamp": n.get("timestamp", ""),
                "lida": bool(n.get("lida", False)),
            }
        )
    for prov in _alertas_proativos(user_id, item):
        if str(prov.get("id", "")) in descartadas:
            continue
        lista.append(prov)
    lista.sort(key=lambda x: str(x.get("timestamp", "")), reverse=True)
    nao_lidas = sum(1 for x in lista if not x.get("lida"))
    return _resposta(200, {"notificacoes": lista, "nao_lidas": nao_lidas})


def _marcar_lidas(user_id: str) -> dict[str, Any]:
    """Marca as notificações de mercado armazenadas como lidas (as proativas são vivas)."""
    item = _ler_user(user_id)
    stored = item.get("notificacoes", []) or []
    if stored:
        for n in stored:
            n["lida"] = True
        try:
            _tabela_users().update_item(
                Key={"userId": user_id},
                UpdateExpression="SET notificacoes = :n",
                ExpressionAttributeValues={":n": stored},
            )
        except Exception:  # noqa: BLE001 - marcar como lida não é crítico
            pass
    return _resposta(200, {"ok": True})


def _limpar_notificacoes(user_id: str) -> dict[str, Any]:
    """Descarta todas as notificações atualmente visíveis para o usuário.

    Diferente de ``_marcar_lidas`` (que só zera o badge de "não-lida" mas
    mantém a lista): apaga as notificações de mercado armazenadas E guarda
    os IDs dos alertas proativos atuais em ``notificacoes_descartadas``,
    para não reaparecerem na próxima listagem enquanto a condição durar
    (ver ``_listar_notificacoes``). Um alerta proativo com ID novo (ex.:
    outra concentração excessiva em ativo diferente) volta a aparecer
    normalmente — o descarte é por ID, não por tipo.
    """
    item = _ler_user(user_id)
    proativos = _alertas_proativos(user_id, item)
    ids_atuais = [str(p.get("id", "")) for p in proativos if p.get("id")]
    descartadas_antes = [str(x) for x in (item.get("notificacoes_descartadas", []) or [])]
    novas_descartadas = sorted(set(descartadas_antes + ids_atuais))
    try:
        _tabela_users().update_item(
            Key={"userId": user_id},
            UpdateExpression=(
                "SET notificacoes = :vazia, notificacoes_descartadas = :d"
            ),
            ExpressionAttributeValues={":vazia": [], ":d": novas_descartadas},
        )
    except Exception as exc:  # noqa: BLE001
        return _resposta(502, {"erro": f"Não foi possível limpar: {exc}"})
    return _resposta(200, {"ok": True})


def _listar_limiares(user_id: str) -> dict[str, Any]:
    """Retorna os limiares de alerta configurados pelo usuário."""
    item = _ler_user(user_id)
    limiares = [
        {
            "indicator_id": str(l.get("indicator_id", "")).upper(),
            "limiar_percentual": _num(l.get("limiar_percentual")),
        }
        for l in (item.get("limiares_alerta", []) or [])
    ]
    return _resposta(200, {"limiares": limiares})


def _salvar_limiares(user_id: str, entrada: Any) -> dict[str, Any]:
    """Substitui os limiares de alerta do usuário (validando indicador e valor)."""
    from decimal import Decimal

    if not isinstance(entrada, list):
        return _resposta(400, {"erro": "Campo 'limiares' deve ser uma lista."})
    validos = []
    for l in entrada:
        if not isinstance(l, dict):
            continue
        ind = str(l.get("indicator_id", "")).upper()
        val = _num(l.get("limiar_percentual"))
        if ind in _INDICADORES_ALERTA and val > 0:
            validos.append(
                {"indicator_id": ind, "limiar_percentual": Decimal(str(round(val, 2)))}
            )
    try:
        _tabela_users().update_item(
            Key={"userId": user_id},
            UpdateExpression="SET limiares_alerta = :l",
            ExpressionAttributeValues={":l": validos},
        )
    except Exception as exc:  # noqa: BLE001
        return _resposta(502, {"erro": f"Não foi possível salvar os limiares: {exc}"})
    return _resposta(
        200,
        {
            "limiares": [
                {
                    "indicator_id": l["indicator_id"],
                    "limiar_percentual": float(l["limiar_percentual"]),
                }
                for l in validos
            ]
        },
    )


def handler(evento: dict[str, Any], contexto: Any) -> dict[str, Any]:
    """Roteia HTTP (``/chat``, ``/gateway/{tool}``) e a execução worker do chat."""
    # Invocação assíncrona (worker do job de chat) — não é um evento HTTP.
    if evento.get("_worker"):
        return _processar_worker(evento)

    token = _bearer(evento.get("headers") or {})
    user_id = _user_id_do_jwt(evento)
    if not token or not user_id:
        return _resposta(401, {"erro": "Requisicao nao autenticada."})

    route_key = evento.get("routeKey", "")
    rota = route_key.split(" ", 1)[-1] if route_key else (evento.get("rawPath") or "")
    corpo = _corpo_json(evento)

    if rota == "/chat":
        acao = corpo.get("acao")
        # Consulta de status de um job de chat (polling).
        if acao == "status":
            return _status_job(corpo.get("jobId"), user_id)
        # Histórico de conversas.
        if acao == "listar_conversas":
            return _listar_conversas(user_id)
        if acao == "obter_conversa":
            return _obter_conversa(corpo.get("conversaId"), user_id)
        if acao == "excluir_conversa":
            return _excluir_conversa(corpo.get("conversaId"), user_id)
        # Carteira do investidor (cadastro manual).
        if acao == "listar_portfolio":
            return _listar_portfolio(user_id)
        if acao == "salvar_portfolio":
            return _salvar_portfolio(user_id, corpo.get("ativos"))
        # Série histórica dos indicadores (gráfico do dashboard).
        if acao == "serie_historica":
            return _resposta(200, {"series": _serie_historica()})
        # Notificações e alertas de mercado configuráveis.
        if acao == "listar_notificacoes":
            return _listar_notificacoes(user_id)
        if acao == "marcar_lidas":
            return _marcar_lidas(user_id)
        if acao == "limpar_notificacoes":
            return _limpar_notificacoes(user_id)
        if acao == "listar_limiares":
            return _listar_limiares(user_id)
        if acao == "salvar_limiares":
            return _salvar_limiares(user_id, corpo.get("limiares"))

        payload = dict(corpo)
        payload["user_id"] = user_id  # sempre do JWT

        # avaliar_perfil é rápido → síncrono (cabe nos 30s do API Gateway).
        if acao == "avaliar_perfil":
            status, resultado = _invocar_runtime(payload, token, user_id)
            chave = "resultado" if status < 400 else "erro"
            return _resposta(status if status < 400 else 502, {chave: resultado})

        # Chat completo (orquestrador) → job assíncrono (evita o teto de 30s).
        # Cria/usa a conversa, grava a mensagem do usuário e dispara o worker.
        mensagem = str(corpo.get("mensagem") or "")
        conversa_id = corpo.get("conversaId") or _criar_conversa(user_id, mensagem)
        # O container usa o conversaId como sessão da AgentCore Memory (contexto
        # de curto prazo por conversa); precisa chegar ao Runtime no payload.
        payload["conversaId"] = conversa_id
        if mensagem:
            _append_mensagem(
                user_id,
                conversa_id,
                {"autor": "user", "texto": mensagem, "hora": _hora()},
            )
        nome_funcao = getattr(contexto, "function_name", None) or os.getenv(
            "AWS_LAMBDA_FUNCTION_NAME", ""
        )
        inicio = _iniciar_job_chat(
            payload, token, user_id, nome_funcao, conversa_id
        )
        inicio["conversaId"] = conversa_id
        return _resposta(202, inicio)

    if rota.startswith("/gateway/"):
        params = evento.get("pathParameters") or {}
        tool = params.get("tool") or rota.split("/gateway/", 1)[-1]
        if not tool:
            return _resposta(400, {"erro": "Tool nao informada."})
        # Argumentos do cliente + user_id autoritativo do JWT (override).
        argumentos = dict(corpo.get("arguments") or corpo)
        argumentos.pop("arguments", None)
        argumentos["user_id"] = user_id
        status, resultado = _chamar_tool_gateway(tool, argumentos, token)
        chave = "resultado" if status < 400 else "erro"
        return _resposta(status if status < 400 else 502, {chave: resultado})

    return _resposta(404, {"erro": "Rota nao encontrada."})
