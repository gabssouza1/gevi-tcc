#!/usr/bin/env python3
"""Ingestão dos documentos do Knowledge Base (tarefa 8.3).

Automatiza duas etapas, de forma idempotente:

1. **Upload** dos documentos locais de ``kb-documentos/`` para o prefixo
   ``kb-documentos/`` do bucket ``s3-investimentos`` (ver ``design.md`` — Buckets
   S3). Preserva a estrutura de subpastas (educacao-financeira, regulamentacao,
   metodologias, produtos-financeiros, faq).
2. **Ingestão** no Bedrock Knowledge Base via ``StartIngestionJob``, com polling
   do status até a conclusão (``COMPLETE``/``FAILED``).

Parametrização (env var ou argumento de linha de comando; o argumento tem
precedência sobre a env var):

| Parâmetro          | Env var             | Argumento              |
|--------------------|---------------------|------------------------|
| Bucket S3          | ``KB_BUCKET``       | ``--bucket``           |
| Knowledge Base ID  | ``KB_ID``           | ``--knowledge-base-id``|
| Data Source ID     | ``KB_DATA_SOURCE_ID``| ``--data-source-id``  |
| Região AWS         | ``AWS_REGION``      | ``--region``           |
| Profile AWS        | ``AWS_PROFILE``     | ``--profile``          |
| Pasta local        | (padrão: kb-documentos)| ``--pasta-local``   |
| Prefixo S3         | (padrão: kb-documentos/)| ``--prefixo-s3``   |

Os valores de ``KB_ID`` e ``KB_DATA_SOURCE_ID`` são obtidos após o deploy da
tarefa 8.2 (stack ``MAIA-<ambiente>-KnowledgeBase``); o bucket é o
``s3-investimentos`` (com o prefixo de ambiente, ex.: ``dev-s3-investimentos``).

Idempotência:

- O upload compara o hash MD5 local com o ``ETag`` do objeto no S3 e só envia
  arquivos novos ou alterados (upload condicional).
- ``StartIngestionJob`` do Bedrock é idempotente por natureza: reprocessa apenas
  o que mudou na data source desde a última ingestão.

Uso (executado somente APÓS o deploy da 8.2, com credenciais válidas):

    python scripts/ingerir_kb.py \\
        --bucket dev-s3-investimentos \\
        --knowledge-base-id XXXXXXXXXX \\
        --data-source-id YYYYYYYYYY \\
        --region us-east-1 --profile contaA

Executar sem ``--knowledge-base-id``/``--data-source-id`` faz apenas o upload
(útil para preparar o bucket antes de a KB existir). Com ``--somente-upload`` a
ingestão é explicitamente pulada.

Nota de segurança: o script NÃO cria nem apaga recursos AWS; apenas envia
objetos ao bucket existente e dispara o job de ingestão da data source.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time
from pathlib import Path

# Pasta local e prefixo S3 padrão (alinhados ao design.md).
PASTA_LOCAL_PADRAO = "kb-documentos"
PREFIXO_S3_PADRAO = "kb-documentos/"

# Subpastas esperadas do KB (ver design.md — Knowledge Base / Buckets S3).
SUBPASTAS_ESPERADAS = (
    "educacao-financeira",
    "regulamentacao",
    "metodologias",
    "produtos-financeiros",
    "faq",
)

# Extensões de documento suportadas pela ingestão do Bedrock KB (subconjunto
# textual usado por este projeto).
EXTENSOES_SUPORTADAS = (".md", ".txt", ".pdf", ".html", ".csv")

# Estados terminais e de progresso do job de ingestão do Bedrock.
ESTADOS_TERMINAIS = {"COMPLETE", "FAILED", "STOPPED"}

# Intervalo e limite do polling do status do job de ingestão.
INTERVALO_POLLING_SEG = 10
TIMEOUT_POLLING_SEG = 1800  # 30 min


def _boto3_session(profile: str | None, region: str | None):
    """Cria uma sessão boto3, com ``profile``/``region`` opcionais.

    Importa boto3 de forma tardia para que ``--help`` e ``py_compile`` funcionem
    mesmo em ambientes sem a biblioteca instalada.
    """
    import boto3  # import tardio, proposital

    return boto3.Session(
        profile_name=profile or None,
        region_name=region or None,
    )


def _md5_arquivo(caminho: Path) -> str:
    """Calcula o MD5 hexadecimal de um arquivo (para comparar com o ETag S3)."""
    h = hashlib.md5()  # nosec B324 - uso não criptográfico: comparação com ETag S3
    with caminho.open("rb") as f:
        for bloco in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloco)
    return h.hexdigest()


def _etag_remoto(s3_client, bucket: str, chave: str) -> str | None:
    """Retorna o ETag (sem aspas) do objeto no S3, ou ``None`` se não existir.

    Só é comparável ao MD5 local quando o objeto foi enviado em uma única parte
    (uploads simples), o que é o caso destes documentos pequenos.
    """
    from botocore.exceptions import ClientError  # import tardio

    try:
        resposta = s3_client.head_object(Bucket=bucket, Key=chave)
    except ClientError as erro:
        codigo = erro.response.get("Error", {}).get("Code")
        if codigo in ("404", "NoSuchKey", "NotFound"):
            return None
        raise
    return resposta.get("ETag", "").strip('"')


def _coletar_documentos(pasta_local: Path) -> list[Path]:
    """Lista os documentos suportados sob ``pasta_local`` (recursivo)."""
    if not pasta_local.is_dir():
        raise FileNotFoundError(f"Pasta local não encontrada: {pasta_local}")
    arquivos = [
        caminho
        for caminho in sorted(pasta_local.rglob("*"))
        if caminho.is_file() and caminho.suffix.lower() in EXTENSOES_SUPORTADAS
    ]
    return arquivos


def _validar_subpastas(pasta_local: Path) -> None:
    """Avisa (sem interromper) se alguma subpasta esperada do KB estiver ausente."""
    ausentes = [
        nome
        for nome in SUBPASTAS_ESPERADAS
        if not (pasta_local / nome).is_dir()
    ]
    if ausentes:
        print(
            "  aviso: subpastas esperadas ausentes: " + ", ".join(ausentes),
            file=sys.stderr,
        )


def enviar_documentos(
    s3_client,
    bucket: str,
    pasta_local: Path,
    prefixo_s3: str,
) -> int:
    """Envia os documentos locais ao S3 de forma idempotente.

    Preserva a estrutura de subpastas sob ``prefixo_s3`` e só envia arquivos
    novos ou cujo conteúdo mudou (comparando MD5 local com o ETag remoto).

    Retorna a quantidade de arquivos efetivamente enviados.
    """
    _validar_subpastas(pasta_local)
    documentos = _coletar_documentos(pasta_local)
    if not documentos:
        print(f"  nenhum documento encontrado em {pasta_local}")
        return 0

    prefixo = prefixo_s3 if prefixo_s3.endswith("/") else prefixo_s3 + "/"
    enviados = 0
    for caminho in documentos:
        rel = caminho.relative_to(pasta_local).as_posix()
        chave = f"{prefixo}{rel}"
        md5_local = _md5_arquivo(caminho)
        if _etag_remoto(s3_client, bucket, chave) == md5_local:
            print(f"  inalterado: {chave}")
            continue
        s3_client.upload_file(str(caminho), bucket, chave)
        enviados += 1
        print(f"  enviado:    {chave}")

    print(f"  upload concluído: {enviados}/{len(documentos)} arquivo(s) enviado(s)")
    return enviados


def iniciar_ingestao(
    bedrock_agent_client,
    knowledge_base_id: str,
    data_source_id: str,
) -> str:
    """Dispara o ``StartIngestionJob`` e retorna o ``ingestionJobId``."""
    resposta = bedrock_agent_client.start_ingestion_job(
        knowledgeBaseId=knowledge_base_id,
        dataSourceId=data_source_id,
        description="Ingestão automatizada dos documentos do KB (tarefa 8.3).",
    )
    job = resposta["ingestionJob"]
    print(
        f"  ingestão iniciada: job={job['ingestionJobId']} "
        f"status={job['status']}"
    )
    return job["ingestionJobId"]


def aguardar_ingestao(
    bedrock_agent_client,
    knowledge_base_id: str,
    data_source_id: str,
    ingestion_job_id: str,
    intervalo: int = INTERVALO_POLLING_SEG,
    timeout: int = TIMEOUT_POLLING_SEG,
) -> dict:
    """Faz polling do status do job até um estado terminal ou timeout.

    Retorna o objeto ``ingestionJob`` final. Lança ``TimeoutError`` se o job não
    atingir um estado terminal dentro de ``timeout`` segundos.
    """
    inicio = time.monotonic()
    while True:
        resposta = bedrock_agent_client.get_ingestion_job(
            knowledgeBaseId=knowledge_base_id,
            dataSourceId=data_source_id,
            ingestionJobId=ingestion_job_id,
        )
        job = resposta["ingestionJob"]
        status = job["status"]
        print(f"  status do job: {status}")
        if status in ESTADOS_TERMINAIS:
            _imprimir_estatisticas(job)
            return job
        if time.monotonic() - inicio > timeout:
            raise TimeoutError(
                f"Timeout aguardando a ingestão (job={ingestion_job_id})."
            )
        time.sleep(intervalo)


def _imprimir_estatisticas(job: dict) -> None:
    """Imprime estatísticas de indexação do job, quando disponíveis."""
    stats = job.get("statistics") or {}
    if not stats:
        return
    print(
        "  indexação: "
        f"scanned={stats.get('numberOfDocumentsScanned', '?')} "
        f"indexed={stats.get('numberOfNewDocumentsIndexed', '?')} "
        f"modified={stats.get('numberOfModifiedDocumentsIndexed', '?')} "
        f"failed={stats.get('numberOfDocumentsFailed', '?')}"
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Monta o parser de argumentos, com defaults vindos de variáveis de ambiente."""
    parser = argparse.ArgumentParser(
        description=(
            "Faz upload dos documentos de kb-documentos/ para o S3 e dispara a "
            "ingestão do Bedrock Knowledge Base (idempotente)."
        ),
    )
    parser.add_argument(
        "--bucket",
        default=os.environ.get("KB_BUCKET"),
        help="Bucket s3-investimentos (env: KB_BUCKET).",
    )
    parser.add_argument(
        "--knowledge-base-id",
        default=os.environ.get("KB_ID"),
        help="ID da Knowledge Base do Bedrock (env: KB_ID).",
    )
    parser.add_argument(
        "--data-source-id",
        default=os.environ.get("KB_DATA_SOURCE_ID"),
        help="ID da data source S3 da KB (env: KB_DATA_SOURCE_ID).",
    )
    parser.add_argument(
        "--region",
        default=os.environ.get("AWS_REGION", "us-east-1"),
        help="Região AWS (env: AWS_REGION; padrão us-east-1).",
    )
    parser.add_argument(
        "--profile",
        default=os.environ.get("AWS_PROFILE"),
        help="Profile AWS CLI (env: AWS_PROFILE; ex.: contaA).",
    )
    parser.add_argument(
        "--pasta-local",
        default=PASTA_LOCAL_PADRAO,
        help=f"Pasta local dos documentos (padrão: {PASTA_LOCAL_PADRAO}).",
    )
    parser.add_argument(
        "--prefixo-s3",
        default=PREFIXO_S3_PADRAO,
        help=f"Prefixo de destino no bucket (padrão: {PREFIXO_S3_PADRAO}).",
    )
    parser.add_argument(
        "--somente-upload",
        action="store_true",
        help="Faz apenas o upload; não dispara a ingestão.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Ponto de entrada: valida argumentos, faz upload e (opcional) ingestão."""
    args = _parse_args(argv)

    if not args.bucket:
        print(
            "erro: informe o bucket via --bucket ou a env var KB_BUCKET.",
            file=sys.stderr,
        )
        return 2

    # Resolve a pasta local relativa à raiz do projeto (pai de scripts/).
    pasta_local = Path(args.pasta_local)
    if not pasta_local.is_absolute():
        raiz_projeto = Path(__file__).resolve().parent.parent
        pasta_local = (raiz_projeto / pasta_local).resolve()

    sessao = _boto3_session(args.profile, args.region)
    s3_client = sessao.client("s3")

    print(f"[1/2] Upload de {pasta_local} -> s3://{args.bucket}/{args.prefixo_s3}")
    try:
        enviar_documentos(s3_client, args.bucket, pasta_local, args.prefixo_s3)
    except FileNotFoundError as erro:
        print(f"erro: {erro}", file=sys.stderr)
        return 1

    if args.somente_upload:
        print("[2/2] Ingestão pulada (--somente-upload).")
        return 0

    if not (args.knowledge_base_id and args.data_source_id):
        print(
            "[2/2] Ingestão pulada: informe --knowledge-base-id e "
            "--data-source-id (ou KB_ID/KB_DATA_SOURCE_ID) após o deploy da 8.2.",
        )
        return 0

    print("[2/2] Ingestão no Bedrock Knowledge Base")
    bedrock_agent = sessao.client("bedrock-agent")
    job_id = iniciar_ingestao(
        bedrock_agent, args.knowledge_base_id, args.data_source_id
    )
    job = aguardar_ingestao(
        bedrock_agent, args.knowledge_base_id, args.data_source_id, job_id
    )
    if job["status"] != "COMPLETE":
        print(f"erro: ingestão terminou com status {job['status']}.", file=sys.stderr)
        return 1
    print("Ingestão concluída com sucesso.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
