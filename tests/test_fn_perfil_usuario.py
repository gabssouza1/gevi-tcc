"""Testes unitários da Lambda ``fn-perfil-usuario`` (Tarefa 5.2).

Cobrem o CRUD do Perfil_Investidor na tabela DynamoDB ``Users`` usando ``moto``
(via as fixtures ``dynamodb_simulado`` e ``nomes_tabelas`` do ``conftest.py``),
sem acessar a nuvem.

Casos exercitados:
- cadastro válido (Requisito 1.1);
- dados inválidos com mensagens específicas por campo (Requisito 1.2);
- e-mail já registrado por outro usuário (Requisito 1.3);
- leitura, atualização e remoção de perfis, incluindo id inexistente;
- operação não suportada.

O diretório ``lambdas/fn-perfil-usuario`` não é um pacote (tem hífen no nome),
então o ``handler`` é carregado dinamicamente via ``importlib`` a partir do
caminho do arquivo. Os testes usam preferencialmente ``processar(event, repo)``
com um ``RepositorioUsuarios`` injetado sobre a tabela simulada; dois casos
exercitam também o ``handler`` completo (com env ``TABELA_USERS``).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

# ---------------------------------------------------------------------------
# Carregamento dinâmico do handler (diretório com hífen não é importável)
# ---------------------------------------------------------------------------
_CAMINHO_HANDLER = (
    Path(__file__).resolve().parents[1]
    / "lambdas"
    / "fn-perfil-usuario"
    / "handler.py"
)


def _carregar_handler() -> ModuleType:
    """Importa o módulo ``handler.py`` da Lambda a partir do caminho do arquivo."""
    spec = importlib.util.spec_from_file_location(
        "fn_perfil_usuario_handler", _CAMINHO_HANDLER
    )
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


handler_mod = _carregar_handler()


# ---------------------------------------------------------------------------
# Fixtures e helpers locais
# ---------------------------------------------------------------------------
@pytest.fixture
def repositorio(dynamodb_simulado, nomes_tabelas):
    """``RepositorioUsuarios`` ligado à tabela ``Users`` simulada (moto)."""
    tabela = dynamodb_simulado.Table(nomes_tabelas["users"])
    return handler_mod.RepositorioUsuarios(tabela)


def _dados_validos(**overrides):
    """Monta um dicionário de perfil válido, permitindo sobrescrever campos."""
    dados = {
        "user_id": "u-1",
        "email": "ana@exemplo.com",
        "nome": "Ana Investidora",
        "nivel_conhecimento": "basico",
        "tolerancia_risco": "CONSERVADOR",
        "objetivos": ["aposentadoria"],
    }
    dados.update(overrides)
    return dados


def _evento(operacao, perfil=None, **extra):
    """Monta um ``event`` no formato aceito pelo handler."""
    evento = {"operacao": operacao}
    if perfil is not None:
        evento["perfil"] = perfil
    evento.update(extra)
    return evento


# ---------------------------------------------------------------------------
# Cadastro válido (Requisito 1.1)
# ---------------------------------------------------------------------------
@pytest.mark.aws
def test_criar_perfil_valido_persiste_no_dynamodb(repositorio, dynamodb_simulado, nomes_tabelas):
    """Cadastro com dados válidos cria a conta e grava o item em ``Users``."""
    resposta = handler_mod.processar(_evento("criar", _dados_validos()), repositorio)

    assert resposta["sucesso"] is True
    assert resposta["operacao"] == "criar"
    assert resposta["perfil"]["user_id"] == "u-1"
    assert resposta["perfil"]["tolerancia_risco"] == "CONSERVADOR"

    # O item foi realmente persistido, com a chave física ``userId``.
    tabela = dynamodb_simulado.Table(nomes_tabelas["users"])
    item = tabela.get_item(Key={"userId": "u-1"}).get("Item")
    assert item is not None
    assert item["email"] == "ana@exemplo.com"


# ---------------------------------------------------------------------------
# Dados inválidos com mensagens específicas por campo (Requisito 1.2)
# ---------------------------------------------------------------------------
@pytest.mark.aws
def test_criar_perfil_campos_obrigatorios_ausentes(repositorio):
    """Campos obrigatórios ausentes geram mensagem específica para cada campo."""
    resposta = handler_mod.processar(
        _evento("criar", {"user_id": "u-2"}), repositorio
    )

    assert resposta["sucesso"] is False
    assert resposta["codigo"] == handler_mod.COD_DADOS_INVALIDOS
    campos = resposta["campos"]
    for campo in ("email", "nome", "nivel_conhecimento", "tolerancia_risco"):
        assert campo in campos
        assert campos[campo]


@pytest.mark.aws
def test_criar_perfil_email_e_enum_invalidos(repositorio):
    """E-mail e enums inválidos retornam mensagem por campo, sem parar no 1º erro."""
    dados = _dados_validos(
        email="sem-arroba",
        nivel_conhecimento="expert",
        tolerancia_risco="RADICAL",
    )
    resposta = handler_mod.processar(_evento("criar", dados), repositorio)

    assert resposta["sucesso"] is False
    assert resposta["codigo"] == handler_mod.COD_DADOS_INVALIDOS
    campos = resposta["campos"]
    assert "email" in campos
    assert "nivel_conhecimento" in campos
    assert "tolerancia_risco" in campos


@pytest.mark.aws
def test_criar_perfil_objetivos_tipo_invalido(repositorio):
    """``objetivos`` com tipo inválido é reportado como erro de campo."""
    resposta = handler_mod.processar(
        _evento("criar", _dados_validos(objetivos="aposentadoria")), repositorio
    )

    assert resposta["sucesso"] is False
    assert "objetivos" in resposta["campos"]


# ---------------------------------------------------------------------------
# E-mail já registrado por outro usuário (Requisito 1.3)
# ---------------------------------------------------------------------------
@pytest.mark.aws
def test_criar_perfil_email_duplicado(repositorio):
    """Criar um segundo perfil com e-mail já em uso retorna EMAIL_DUPLICADO."""
    primeiro = handler_mod.processar(_evento("criar", _dados_validos()), repositorio)
    assert primeiro["sucesso"] is True

    # Mesmo e-mail, outro user_id.
    dados_conflito = _dados_validos(user_id="u-2", email="ANA@exemplo.com")
    resposta = handler_mod.processar(_evento("criar", dados_conflito), repositorio)

    assert resposta["sucesso"] is False
    assert resposta["codigo"] == handler_mod.COD_EMAIL_DUPLICADO


@pytest.mark.aws
def test_criar_perfil_user_id_duplicado(repositorio):
    """Criar dois perfis com o mesmo ``user_id`` é rejeitado como duplicado."""
    handler_mod.processar(_evento("criar", _dados_validos()), repositorio)
    resposta = handler_mod.processar(
        _evento("criar", _dados_validos(email="outra@exemplo.com")), repositorio
    )

    assert resposta["sucesso"] is False
    assert resposta["codigo"] == handler_mod.COD_EMAIL_DUPLICADO


# ---------------------------------------------------------------------------
# Leitura, atualização e remoção
# ---------------------------------------------------------------------------
@pytest.mark.aws
def test_ler_perfil_existente(repositorio):
    """Ler um perfil existente devolve os dados persistidos."""
    handler_mod.processar(_evento("criar", _dados_validos()), repositorio)

    resposta = handler_mod.processar(_evento("ler", user_id="u-1"), repositorio)

    assert resposta["sucesso"] is True
    assert resposta["perfil"]["email"] == "ana@exemplo.com"
    assert resposta["perfil"]["nome"] == "Ana Investidora"


@pytest.mark.aws
def test_ler_perfil_inexistente(repositorio):
    """Ler um ``user_id`` inexistente retorna NAO_ENCONTRADO."""
    resposta = handler_mod.processar(_evento("ler", user_id="nao-existe"), repositorio)

    assert resposta["sucesso"] is False
    assert resposta["codigo"] == handler_mod.COD_NAO_ENCONTRADO


@pytest.mark.aws
def test_atualizar_perfil_existente(repositorio):
    """Atualizar um perfil existente persiste os novos dados."""
    handler_mod.processar(_evento("criar", _dados_validos()), repositorio)

    dados_atualizados = _dados_validos(
        nome="Ana Atualizada", tolerancia_risco="ARROJADO"
    )
    resposta = handler_mod.processar(
        _evento("atualizar", dados_atualizados), repositorio
    )

    assert resposta["sucesso"] is True
    assert resposta["perfil"]["nome"] == "Ana Atualizada"
    assert resposta["perfil"]["tolerancia_risco"] == "ARROJADO"

    leitura = handler_mod.processar(_evento("ler", user_id="u-1"), repositorio)
    assert leitura["perfil"]["nome"] == "Ana Atualizada"


@pytest.mark.aws
def test_atualizar_perfil_inexistente(repositorio):
    """Atualizar um ``user_id`` inexistente retorna NAO_ENCONTRADO."""
    resposta = handler_mod.processar(
        _evento("atualizar", _dados_validos(user_id="fantasma")), repositorio
    )

    assert resposta["sucesso"] is False
    assert resposta["codigo"] == handler_mod.COD_NAO_ENCONTRADO


@pytest.mark.aws
def test_remover_perfil_existente(repositorio):
    """Remover um perfil existente apaga o item; leitura posterior falha."""
    handler_mod.processar(_evento("criar", _dados_validos()), repositorio)

    resposta = handler_mod.processar(_evento("remover", user_id="u-1"), repositorio)
    assert resposta["sucesso"] is True
    assert resposta["user_id"] == "u-1"

    leitura = handler_mod.processar(_evento("ler", user_id="u-1"), repositorio)
    assert leitura["codigo"] == handler_mod.COD_NAO_ENCONTRADO


@pytest.mark.aws
def test_remover_perfil_inexistente(repositorio):
    """Remover um ``user_id`` inexistente retorna NAO_ENCONTRADO."""
    resposta = handler_mod.processar(
        _evento("remover", user_id="nao-existe"), repositorio
    )

    assert resposta["sucesso"] is False
    assert resposta["codigo"] == handler_mod.COD_NAO_ENCONTRADO


@pytest.mark.aws
def test_operacao_nao_suportada(repositorio):
    """Uma operação desconhecida retorna OPERACAO_INVALIDA."""
    resposta = handler_mod.processar(_evento("teletransportar"), repositorio)

    assert resposta["sucesso"] is False
    assert resposta["codigo"] == handler_mod.COD_OPERACAO_INVALIDA


# ---------------------------------------------------------------------------
# Ponto de entrada completo ``handler`` (usa RepositorioUsuarios.padrao())
# ---------------------------------------------------------------------------
@pytest.mark.aws
def test_handler_criar_e_ler_via_env(dynamodb_simulado, nomes_tabelas, monkeypatch):
    """O ``handler`` completo cria e lê um perfil usando a tabela do ambiente."""
    monkeypatch.setenv("TABELA_USERS", nomes_tabelas["users"])
    # ``NOME_TABELA_USERS`` é lido no import; garante alinhamento com a fixture.
    monkeypatch.setattr(handler_mod, "NOME_TABELA_USERS", nomes_tabelas["users"])

    criado = handler_mod.handler(_evento("criar", _dados_validos(user_id="u-h")), None)
    assert criado["sucesso"] is True

    lido = handler_mod.handler(_evento("ler", user_id="u-h"), None)
    assert lido["sucesso"] is True
    assert lido["perfil"]["user_id"] == "u-h"


@pytest.mark.aws
def test_handler_event_nao_dicionario(dynamodb_simulado):
    """O ``handler`` rejeita um ``event`` que não seja objeto JSON."""
    resposta = handler_mod.handler("payload-invalido", None)

    assert resposta["sucesso"] is False
    assert resposta["codigo"] == handler_mod.COD_DADOS_INVALIDOS
