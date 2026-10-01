"""Entrypoint HTTP do contêiner do AgentCore Runtime (tarefa 9.6).

O Runtime do Amazon Bedrock AgentCore invoca o contêiner pelo contrato HTTP:

- ``GET /ping``          → verificação de saúde (200).
- ``POST /invocations``  → executa uma interação e retorna o resultado (JSON).

Este servidor liga o Agente_Orquestrador (``agentes.executar_orquestrador``),
que por sua vez aciona os 5 agentes especialistas, às dependências reais em
tempo de execução:

- ``chamar_tool``: aciona as tools pelo AgentCore Gateway (MCP), repassando o
  JWT do usuário recebido na requisição (o Gateway valida o token — tarefa 9.2).
- ``recuperar_kb``: RAG do Knowledge Base via Bedrock (``bedrock-agent-runtime``
  ``retrieve``) para o Agente_Explicador em perfis básicos.

Identificadores vêm de variáveis de ambiente injetadas pelo Runtime (ver
``AgentsStack._criar_runtime``): ``GATEWAY_URL``, ``MEMORY_ID``,
``KNOWLEDGE_BASE_ID`` e ``MODELO_LLM``.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from agentes import MODELO_LLM, avaliar_perfil, executar_orquestrador


def _log(evento: str, **campos: Any) -> None:
    """Log estruturado em stdout (capturado pelo CloudWatch do Runtime)."""
    print(json.dumps({"evento": evento, **campos}, default=str), flush=True)

PORTA = int(os.getenv("PORT", "8080"))
GATEWAY_URL = os.getenv("GATEWAY_URL", "")
KNOWLEDGE_BASE_ID = os.getenv("KNOWLEDGE_BASE_ID", "")
MEMORY_ID = os.getenv("MEMORY_ID", "")
REGIAO = os.getenv("AWS_REGION", "us-east-1")
# Tabela de jobs do proxy (dev-ChatJobs): o Runtime grava a etapa atual do
# pipeline aqui, na mesma linha que o proxy já usa para status/resultado —
# é o único jeito de reportar progresso, já que a chamada ao Runtime é uma
# única requisição HTTP síncrona sem canal de volta durante a execução (ver
# docs/decisoes-agentes-e-latencia.md). Falhas ao gravar nunca derrubam a
# resposta: progresso é só cosmético.
JOBS_TABLE = os.getenv("JOBS_TABLE", "dev-ChatJobs")

# Namespaces das estratégias de longo prazo da AgentCore Memory (ver AgentsStack:
# ManagedMemoryStrategy). ``{}`` recebe o actorId (= user_id) do investidor.
_NS_PREFERENCIAS = "/investidor/{}/preferencias"
_NS_FATOS = "/investidor/{}/fatos"
# Quantos pares (turno usuário+IA) recentes recarregar como contexto de curto
# prazo. Contém custo/tokens sem perder o fio da conversa.
_MAX_EVENTOS_HISTORICO = 6

# Cliente Bedrock (Converse) reaproveitado entre invocações do container.
_bedrock = None
# Cliente do data plane da AgentCore Memory (create/list/retrieve), reaproveitado.
_memoria = None
# Cliente DynamoDB (progresso do job), reaproveitado entre invocações.
_dynamo = None


def _criar_reportador_progresso(job_id: str | None) -> Any:
    """Cria um ``ao_progresso(etapa)`` que grava a etapa atual no job (DynamoDB).

    Sem jobId (ex.: chamada fora do fluxo assíncrono de chat), devolve um
    no-op. Erros de gravação só são logados — nunca interrompem o pipeline,
    já que o progresso é puramente informativo para a UI de polling.
    """
    if not job_id:
        return lambda etapa: None

    def ao_progresso(etapa: str) -> None:
        global _dynamo
        try:
            if _dynamo is None:
                import boto3  # import tardio: só no container em runtime

                _dynamo = boto3.client("dynamodb", region_name=REGIAO)
            _dynamo.update_item(
                TableName=JOBS_TABLE,
                Key={"jobId": {"S": job_id}},
                UpdateExpression="SET etapa = :e",
                ExpressionAttributeValues={":e": {"S": str(etapa)[:200]}},
                # Só atualiza um job pendente que já existe — nunca recria um
                # job apagado (mesma lógica de "attribute_exists" usada para
                # não ressuscitar conversas excluídas).
                ConditionExpression="attribute_exists(jobId)",
            )
        except Exception as exc:  # noqa: BLE001 - progresso é só cosmético
            _log("progresso_erro", job_id=job_id, erro=str(exc))

    return ao_progresso


def _chamar_modelo(
    mensagens: list[dict[str, Any]],
    ferramentas: list[dict[str, Any]],
    sistema: str,
    modelo: str = MODELO_LLM,
    tool_choice: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Chama o Claude pela Converse API com tool use.

    ``modelo`` permite ao chamador escolher entre o Opus (Orquestrador e
    Explicador — decisão/redação final) e um modelo mais rápido para agentes
    que só formatam notas internas (ver ``agentes.MODELO_LLM_RAPIDO``).

    ``tool_choice``, se informado, é passado como ``toolConfig.toolChoice`` da
    Converse API (ex.: ``{"tool": {"name": "Agente_Selecao_Ativos"}}`` para
    forçar uma tool específica, em vez do padrão ``auto``). Usado quando uma
    instrução textual não é suficiente para garantir determinismo — ver
    ``agentes._mensagem_pede_dados_de_carteira`` (instruções de prompt "chame
    a tool antes de responder" foram testadas e o modelo as ignorou; forçar a
    tool_choice é a forma garantida da Converse API).

    O modelo decide, a cada turno, se responde ou pede uma ferramenta
    (``stopReason='tool_use'``). Retorna o dicionário bruto da Converse.
    """
    import time as _time

    global _bedrock
    if _bedrock is None:
        import boto3  # import tardio (só no container em runtime)

        _bedrock = boto3.client("bedrock-runtime", region_name=REGIAO)
    kwargs: dict[str, Any] = {
        "modelId": modelo,
        "system": [{"text": sistema}],
        "messages": mensagens,
        # 4096: o Agente_Explicador escreve respostas formatadas (markdown,
        # tabelas) e estava sendo cortado (stop_reason="max_tokens") em
        # recomendações completas com 2048.
        "inferenceConfig": {"maxTokens": 4096, "temperature": 0.2},
    }
    # toolConfig só quando há ferramentas (a Converse rejeita lista vazia).
    if ferramentas:
        tool_config: dict[str, Any] = {"tools": ferramentas}
        if tool_choice:
            tool_config["toolChoice"] = tool_choice
        kwargs["toolConfig"] = tool_config
    _t0 = _time.monotonic()
    resposta = _bedrock.converse(**kwargs)
    _log(
        "chamar_modelo",
        duracao_s=round(_time.monotonic() - _t0, 1),
        stop_reason=resposta.get("stopReason"),
        modelo=modelo,
        # Assinatura do agente/sistema (primeiras palavras) para diferenciar
        # Orquestrador dos especialistas nos logs.
        sistema_prefixo=sistema[:40],
    )
    return resposta


# Mapa {nome_logico → nome_real} das tools do Gateway. O AgentCore registra as
# tools como ``<target>___<tool>``; resolvemos o nome real via tools/list (uma
# vez, cacheado) para não depender do prefixo/ambiente.
_MAPA_TOOLS: dict[str, str] | None = None


def _carregar_mapa_tools(token: str | None) -> dict[str, str]:
    """Carrega e cacheia o mapa de nomes de tools do Gateway (via tools/list)."""
    global _MAPA_TOOLS
    if _MAPA_TOOLS is not None:
        return _MAPA_TOOLS
    mapa: dict[str, str] = {}
    try:
        corpo = json.dumps(
            {"jsonrpc": "2.0", "id": "list", "method": "tools/list", "params": {}}
        ).encode("utf-8")
        cabecalhos = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if token:
            cabecalhos["Authorization"] = token
        req = urllib.request.Request(
            GATEWAY_URL, data=corpo, headers=cabecalhos, method="POST"
        )
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
            payload = _extrair_json_mcp(resp.read().decode("utf-8"))
        tools = payload.get("result", {}).get("tools", []) if isinstance(payload, dict) else []
        for t in tools:
            nome = t.get("name", "")
            sufixo = nome.split("___")[-1] if "___" in nome else nome
            if sufixo:
                mapa[sufixo] = nome
        _MAPA_TOOLS = mapa
        _log("tools_list", tools=list(mapa.keys()))
    except Exception as exc:  # noqa: BLE001
        _log("tools_list_erro", erro=str(exc))
        _MAPA_TOOLS = {}
    return _MAPA_TOOLS


def _criar_chamador_tool(token_autorizacao: str | None):
    """Cria um ``chamar_tool`` que aciona tools pelo Gateway (MCP/JSON-RPC).

    Repassa o JWT do usuário no cabeçalho ``Authorization`` para que o Gateway
    valide o token (authorizer Cognito) antes de rotear para a Lambda da tool.
    Resolve o nome lógico da tool para o nome real registrado no Gateway.
    """

    def chamar_tool(nome_tool: str, argumentos: dict[str, Any]) -> dict[str, Any]:
        nome_real = _carregar_mapa_tools(token_autorizacao).get(nome_tool, nome_tool)
        corpo = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": nome_real,
                "method": "tools/call",
                "params": {"name": nome_real, "arguments": argumentos},
            }
        ).encode("utf-8")
        cabecalhos = {
            "Content-Type": "application/json",
            # MCP Streamable HTTP: alguns servidores exigem aceitar SSE.
            "Accept": "application/json, text/event-stream",
        }
        if token_autorizacao:
            cabecalhos["Authorization"] = token_autorizacao
        _log(
            "chamar_tool",
            tool=nome_tool,
            tool_real=nome_real,
            tem_token=bool(token_autorizacao),
        )
        req = urllib.request.Request(
            GATEWAY_URL, data=corpo, headers=cabecalhos, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
                bruto = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detalhe = exc.read().decode("utf-8", "replace")
            _log("gateway_http_error", tool=nome_tool, status=exc.code, corpo=detalhe[:800])
            raise
        except Exception as exc:  # noqa: BLE001
            _log("gateway_erro", tool=nome_tool, erro=str(exc))
            raise
        # A resposta pode vir como SSE (event stream) ou JSON puro.
        payload = _extrair_json_mcp(bruto)
        if isinstance(payload, dict) and "error" in payload:
            _log("gateway_jsonrpc_error", tool=nome_tool, error=payload["error"])
        # JSON-RPC: o resultado fica em ``result``; MCP embrulha em content[].text.
        resultado = payload.get("result", payload) if isinstance(payload, dict) else payload
        return _desembrulhar_mcp(resultado)

    return chamar_tool


def _desembrulhar_mcp(resultado: Any) -> Any:
    """Extrai o payload real de um resultado MCP ``{content:[{text: "<json>"}]}``.

    O AgentCore Gateway devolve a saída da tool embrulhada no formato MCP: uma
    lista ``content`` com blocos ``{type:'text', text:'<json serializado>'}``.
    Desserializa o primeiro bloco de texto; se não for esse formato, retorna como
    veio.
    """
    if isinstance(resultado, dict) and isinstance(resultado.get("content"), list):
        for bloco in resultado["content"]:
            if isinstance(bloco, dict) and bloco.get("text"):
                try:
                    return json.loads(bloco["text"])
                except (ValueError, TypeError):
                    return {"texto": bloco["text"]}
    return resultado


def _extrair_json_mcp(bruto: str) -> Any:
    """Decodifica a resposta MCP, aceitando JSON puro ou event-stream (SSE)."""
    texto = bruto.strip()
    try:
        return json.loads(texto)
    except (ValueError, TypeError):
        pass
    # SSE: procura a última linha ``data: {...}``.
    for linha in reversed(texto.splitlines()):
        linha = linha.strip()
        if linha.startswith("data:"):
            try:
                return json.loads(linha[len("data:"):].strip())
            except (ValueError, TypeError):
                continue
    return {"raw": bruto}


def _recuperar_kb(consulta: str) -> list[str]:
    """Recupera trechos do Knowledge Base (RAG) via Bedrock, se configurado."""
    if not KNOWLEDGE_BASE_ID:
        return []
    import time as _time

    import boto3  # import tardio: só quando há KB configurado

    _t0 = _time.monotonic()
    cliente = boto3.client("bedrock-agent-runtime", region_name=REGIAO)
    resposta = cliente.retrieve(
        knowledgeBaseId=KNOWLEDGE_BASE_ID,
        retrievalQuery={"text": consulta},
    )
    _log("recuperar_kb", consulta=consulta[:80], duracao_s=round(_time.monotonic() - _t0, 1))
    return [
        item.get("content", {}).get("text", "")
        for item in resposta.get("retrievalResults", [])
        if item.get("content", {}).get("text")
    ]


# ---------------------------------------------------------------------------
# AgentCore Memory — memória do agente (parte "híbrida": DynamoDB serve a UI;
# a Memory dá ao agente contexto de curto prazo (turnos da conversa) e de longo
# prazo (preferências/fatos do investidor entre conversas). ``actorId`` = user_id,
# ``sessionId`` = conversaId. Falhas de memória nunca derrubam a resposta.
# ---------------------------------------------------------------------------
def _cliente_memoria():
    """Cria (e cacheia) o cliente do data plane da AgentCore Memory."""
    global _memoria
    if _memoria is None:
        import boto3  # import tardio: só no container em runtime

        _memoria = boto3.client("bedrock-agentcore", region_name=REGIAO)
    return _memoria


def _carregar_historico(user_id: str | None, sessao_id: str | None) -> list[tuple[str, str]]:
    """Curto prazo: recarrega os turnos anteriores desta conversa (list_events).

    Retorna uma lista ordenada de ``(papel, texto)`` com papel em
    ``{'USER','ASSISTANT'}`` — apenas os últimos ``_MAX_EVENTOS_HISTORICO`` pares,
    preservando os pares completos (cada evento guarda um turno usuário+IA).
    """
    if not (MEMORY_ID and user_id and sessao_id):
        return []
    try:
        resp = _cliente_memoria().list_events(
            memoryId=MEMORY_ID,
            actorId=user_id,
            sessionId=sessao_id,
            includePayloads=True,
            maxResults=100,
        )
    except Exception as exc:  # noqa: BLE001 - memória é complementar
        _log("memoria_historico_erro", erro=str(exc))
        return []
    eventos = resp.get("events", []) or []
    # Ordena por timestamp asc e mantém só os pares mais recentes (pares completos).
    eventos.sort(key=lambda e: str(e.get("eventTimestamp", "")))
    eventos = eventos[-_MAX_EVENTOS_HISTORICO:]
    turnos: list[tuple[str, str]] = []
    for ev in eventos:
        for item in ev.get("payload", []) or []:
            conv = item.get("conversational") or {}
            texto = (conv.get("content") or {}).get("text", "")
            papel = (conv.get("role") or "").upper()
            if texto and papel in ("USER", "ASSISTANT"):
                turnos.append((papel, texto))
    return turnos


def _recuperar_memoria_longo_prazo(user_id: str | None, consulta: str) -> list[str]:
    """Longo prazo: preferências e fatos do investidor (retrieve_memory_records).

    Busca semântica nos namespaces de preferências e fatos (escopados por
    ``actorId`` = user_id) usando a mensagem atual como consulta. Retorna os
    textos dos insights mais relevantes (sem duplicatas).
    """
    if not (MEMORY_ID and user_id and consulta):
        return []
    cli = _cliente_memoria()
    insights: list[str] = []
    vistos: set[str] = set()
    for template in (_NS_PREFERENCIAS, _NS_FATOS):
        namespace = template.format(user_id)
        try:
            resp = cli.retrieve_memory_records(
                memoryId=MEMORY_ID,
                namespace=namespace,
                searchCriteria={"searchQuery": consulta, "topK": 3},
            )
        except Exception as exc:  # noqa: BLE001 - memória é complementar
            _log("memoria_longo_erro", namespace=namespace, erro=str(exc))
            continue
        for rec in resp.get("memoryRecordSummaries", []) or []:
            texto = (rec.get("content") or {}).get("text", "").strip()
            if texto and texto not in vistos:
                vistos.add(texto)
                insights.append(texto)
    return insights


def _salvar_turno(
    user_id: str | None, sessao_id: str | None, mensagem_usuario: str, resposta_ia: str
) -> None:
    """Grava o turno (usuário + IA) na Memory (create_event).

    Isso alimenta a memória de curto prazo (recarregada na próxima mensagem) e
    dispara, de forma assíncrona, a extração das memórias de longo prazo
    (preferências/fatos) pelas estratégias ativas da Memory.
    """
    if not (MEMORY_ID and user_id and sessao_id and (mensagem_usuario or resposta_ia)):
        return
    from datetime import datetime, timezone

    payload = []
    if mensagem_usuario:
        payload.append(
            {"conversational": {"content": {"text": mensagem_usuario}, "role": "USER"}}
        )
    if resposta_ia:
        payload.append(
            {"conversational": {"content": {"text": resposta_ia}, "role": "ASSISTANT"}}
        )
    try:
        _cliente_memoria().create_event(
            memoryId=MEMORY_ID,
            actorId=user_id,
            sessionId=sessao_id,
            eventTimestamp=datetime.now(timezone.utc),
            payload=payload,
        )
    except Exception as exc:  # noqa: BLE001 - persistência de memória não derruba
        _log("memoria_salvar_erro", erro=str(exc))


class Manipulador(BaseHTTPRequestHandler):
    """Implementa o contrato HTTP do AgentCore Runtime (``/ping``, ``/invocations``)."""

    def _responder(self, codigo: int, corpo: dict[str, Any]) -> None:
        dados = json.dumps(corpo).encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(dados)))
        self.end_headers()
        self.wfile.write(dados)

    def do_GET(self) -> None:  # noqa: N802 - assinatura da BaseHTTPRequestHandler
        if self.path == "/ping":
            self._responder(200, {"status": "healthy"})
        else:
            self._responder(404, {"erro": "rota nao encontrada"})

    def do_POST(self) -> None:  # noqa: N802 - assinatura da BaseHTTPRequestHandler
        if self.path != "/invocations":
            self._responder(404, {"erro": "rota nao encontrada"})
            return
        try:
            tamanho = int(self.headers.get("Content-Length", "0"))
            evento = json.loads(self.rfile.read(tamanho) or b"{}")
            autorizacao = self.headers.get("Authorization")
            _log(
                "invocacao",
                tem_authorization=bool(autorizacao),
                headers=sorted(self.headers.keys()),
                user_id=evento.get("user_id") or evento.get("userId"),
            )
            chamar_tool = _criar_chamador_tool(autorizacao)
            if evento.get("acao") == "avaliar_perfil":
                # Agente_Perfil: questionário de suitability (sem loop de tools).
                resultado = avaliar_perfil(evento, chamar_tool, _chamar_modelo)
                _log(
                    "avaliar_perfil_ok",
                    user_id=resultado.get("user_id"),
                    perfil=resultado.get("perfil"),
                    pontuacao=resultado.get("pontuacao"),
                    persistido=resultado.get("persistido"),
                )
            else:
                # Memória do agente: conversaId = sessão da Memory. Carrega o
                # contexto de curto prazo (turnos anteriores) e de longo prazo
                # (preferências/fatos) antes de decidir, e grava o turno depois.
                user_id_mem = evento.get("user_id") or evento.get("userId")
                sessao_mem = evento.get("conversaId") or evento.get("conversa_id")
                mensagem_atual = (
                    evento.get("mensagem")
                    or evento.get("prompt")
                    or "Monte uma recomendação de investimento adequada ao meu perfil."
                )
                evento["historico"] = _carregar_historico(user_id_mem, sessao_mem)
                evento["memorias"] = _recuperar_memoria_longo_prazo(
                    user_id_mem, str(mensagem_atual)
                )
                ao_progresso = _criar_reportador_progresso(evento.get("jobId"))
                resultado = executar_orquestrador(
                    evento, chamar_tool, _chamar_modelo, _recuperar_kb, ao_progresso
                )
                _salvar_turno(
                    user_id_mem,
                    sessao_mem,
                    str(mensagem_atual),
                    resultado.get("resposta", ""),
                )
                _log(
                    "invocacao_ok",
                    user_id=resultado.get("user_id"),
                    agentes_usados=resultado.get("agentes_usados"),
                    ferramentas_usadas=resultado.get("ferramentas_usadas"),
                    mem_turnos=len(evento.get("historico") or []),
                    mem_longo_prazo=len(evento.get("memorias") or []),
                )
            self._responder(200, resultado)
        except Exception as exc:  # noqa: BLE001 - fronteira HTTP: nunca vazar stack
            _log("erro_pipeline", erro=str(exc), traceback=traceback.format_exc())
            self._responder(500, {"erro": f"falha ao processar: {exc}"})

    def log_message(self, *_args: Any) -> None:  # silencia log padrão ruidoso
        return


def main() -> None:
    """Sobe o servidor HTTP na porta esperada pelo Runtime (8080)."""
    servidor = ThreadingHTTPServer(("0.0.0.0", PORTA), Manipulador)
    servidor.serve_forever()


if __name__ == "__main__":
    main()
