"""Logging estruturado (JSON) com correlação de requisição (Tarefa 11.1).

As 6 Lambdas são criadas com ``logging_format = JSON`` (ComputeStack), então o
Amazon CloudWatch já recebe cada registro como JSON com ``requestId``, nível e
timestamp adicionados pela plataforma (Advanced Logging Controls). Este módulo
padroniza o conteúdo dos registros: cada evento é emitido como um objeto JSON
(no campo de mensagem) contendo o nome do evento e campos estruturados, além do
``request_id`` e do nome da função (correlação por invocação), obtidos do
``context`` da Lambda.

Uso típico no handler:

    from shared import obter_logger, log_evento

    logger = obter_logger(__name__)

    def handler(event, context):
        log_evento(logger, "invocacao_recebida", context=context, operacao="ler")
        ...
        log_evento(logger, "invocacao_concluida", context=context, sucesso=True)

Requisito coberto: 14.4 (logs de execução em JSON estruturado, úteis para debug).
"""

from __future__ import annotations

import json
import logging
from typing import Any

# Nível padrão de log da aplicação (coerente com application_log_level=INFO).
NIVEL_PADRAO = logging.INFO


def obter_logger(nome: str = "maia") -> logging.Logger:
    """Devolve um logger configurado (idempotente) no nível INFO.

    Seguro para chamar em import de módulo e em cada invocação: não duplica
    handlers. Com ``logging_format = JSON`` na Lambda, os registros emitidos por
    este logger chegam ao CloudWatch já em JSON, com ``requestId`` da invocação.
    """
    logger = logging.getLogger(nome)
    if not logger.handlers and not logging.getLogger().handlers:
        logging.basicConfig(level=NIVEL_PADRAO)
    logger.setLevel(NIVEL_PADRAO)
    return logger


def log_evento(
    logger: logging.Logger,
    evento: str,
    *,
    nivel: int = NIVEL_PADRAO,
    context: Any | None = None,
    **campos: Any,
) -> None:
    """Emite um evento estruturado (JSON) com correlação de requisição.

    O registro contém o nome do ``evento`` e os ``campos`` informados; quando o
    ``context`` da Lambda é passado, inclui ``request_id`` (``aws_request_id``) e
    ``funcao`` (``function_name``) para correlacionar todos os logs de uma mesma
    invocação. A serialização usa ``default=str`` para não quebrar com tipos não
    triviais (ex.: Decimal).
    """
    registro: dict[str, Any] = {"evento": evento, **campos}
    if context is not None:
        registro["request_id"] = getattr(context, "aws_request_id", None)
        registro["funcao"] = getattr(context, "function_name", None)
    logger.log(nivel, json.dumps(registro, default=str, ensure_ascii=False))


def log_erro(
    logger: logging.Logger,
    evento: str,
    *,
    context: Any | None = None,
    **campos: Any,
) -> None:
    """Emite um evento de erro estruturado (nível ERROR) com correlação.

    Atalho para ``log_evento(..., nivel=logging.ERROR)``, evitando que os
    handlers precisem importar ``logging`` só para registrar erros.
    """
    log_evento(logger, evento, nivel=logging.ERROR, context=context, **campos)
