"""Empacotamento das funções Lambda para o CDK (staging sem Docker).

As funções vivem em ``lambdas/<fn>/handler.py`` e importam o pacote comum
``shared`` (na raiz do projeto) com ``from shared import ...``. Para virar um
artefato Lambda, cada função precisa de um diretório autocontido com:

1. o ``handler.py`` da função (ponto de entrada ``handler.handler``);
2. o pacote ``shared/`` vendorizado ao lado do handler;
3. as dependências do ``requirements.txt`` da função, quando houver
   (ex.: ``requests`` em ``fn-consulta-APIs``), também vendorizadas.

Este módulo monta esse diretório em ``infra/build/lambdas/<fn>/`` por cópia de
arquivos (determinística, sem rede) e, quando há ``requirements.txt``, vendoriza
as dependências via ``pip install --target`` (com cache por hash do arquivo).

Optou-se por *staging* em tempo de synth, em vez de ``BundlingOptions`` com
imagem Docker, para que ``cdk synth``/``cdk deploy`` funcionem em ambientes sem
Docker. O conteúdo do diretório é estável entre execuções, então o hash de asset
do CDK (e portanto a identidade do artefato) permanece o mesmo quando o código
não muda.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

# Diretórios base, resolvidos a partir da localização deste módulo (infra/).
_INFRA_DIR = Path(__file__).resolve().parent
_RAIZ_PROJETO = _INFRA_DIR.parent
_DIR_LAMBDAS = _RAIZ_PROJETO / "lambdas"
_DIR_SHARED = _RAIZ_PROJETO / "shared"

# Saída do staging. Fica sob ``infra/build/`` (ignorado pelo Git, ver
# .gitignore ``build/``) para não poluir a árvore versionada.
_DIR_BUILD = _INFRA_DIR / "build" / "lambdas"

# Padrões ignorados ao copiar o handler e o pacote ``shared`` (artefatos de
# desenvolvimento que não devem ir para o pacote de deploy).
_IGNORAR = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", ".pytest_cache")


def _copiar_shared(destino: Path) -> None:
    """Copia o pacote ``shared/`` para ``<destino>/shared`` (sobrescrevendo)."""
    alvo = destino / "shared"
    if alvo.exists():
        shutil.rmtree(alvo)
    shutil.copytree(_DIR_SHARED, alvo, ignore=_IGNORAR)


def _vendorizar_dependencias(origem_fn: Path, destino: Path) -> None:
    """Vendoriza o ``requirements.txt`` da função em ``destino`` (com cache).

    Só reinstala quando o conteúdo do ``requirements.txt`` muda, comparando o
    hash SHA-256 gravado em ``.deps.sha256``. Funções sem ``requirements.txt``
    (a maioria) não disparam nenhuma chamada ao ``pip``.
    """
    requirements = origem_fn / "requirements.txt"
    if not requirements.exists():
        return

    marcador = destino / ".deps.sha256"
    hash_atual = hashlib.sha256(requirements.read_bytes()).hexdigest()
    if marcador.exists() and marcador.read_text(encoding="utf-8").strip() == hash_atual:
        return

    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--quiet",
            "--disable-pip-version-check",
            "-r",
            str(requirements),
            "--target",
            str(destino),
        ],
        check=True,
    )
    marcador.write_text(hash_atual, encoding="utf-8")


def preparar_codigo_lambda(nome_fn: str) -> str:
    """Monta o diretório de artefato da função e devolve seu caminho.

    Copia o ``handler.py`` e o pacote ``shared`` (sempre sobrescritos, operação
    barata) e vendoriza as dependências do ``requirements.txt`` da função quando
    existir (com cache por hash). O caminho retornado é consumido por
    ``aws_lambda.Code.from_asset(...)`` na ``ComputeStack``.

    Args:
        nome_fn: Nome da pasta da função em ``lambdas/`` (ex.: ``fn-perfil-usuario``).

    Returns:
        Caminho absoluto (str) do diretório de artefato pronto para empacotar.

    Raises:
        FileNotFoundError: se a função ou seu ``handler.py`` não existirem.
    """
    origem = _DIR_LAMBDAS / nome_fn
    handler = origem / "handler.py"
    if not handler.exists():
        raise FileNotFoundError(
            f"handler.py não encontrado para a Lambda '{nome_fn}' em {handler}."
        )

    destino = _DIR_BUILD / nome_fn
    destino.mkdir(parents=True, exist_ok=True)

    # 1) handler.py (sobrescreve sempre).
    shutil.copy2(handler, destino / "handler.py")

    # 2) pacote shared/ (sobrescreve sempre).
    _copiar_shared(destino)

    # 3) dependências do requirements.txt da função (cache por hash).
    _vendorizar_dependencias(origem, destino)

    return str(destino)
