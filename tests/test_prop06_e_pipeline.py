"""Prop. 6 (isolamento entre agentes) e integração Orquestrador + especialistas.

- **Propriedade 6 — Isolamento entre Agentes** (Tarefa 9.4): cada agente
  especialista acessa apenas as tools da sua política (design.md — "Controle
  de Acesso por Agente"). Verifica que ``agentes.AGENTES_ESPECIALISTAS`` bate
  com a matriz do design e que não há vazamento de tool entre agentes.
- **Integração ponta a ponta** (Tarefa 12.2): ``executar_orquestrador`` decide
  quais das ferramentas/agentes acionar; cada agente especialista roda seu
  próprio loop de tool-use restrito à própria política, e o Agente_Explicador
  é o último a ser chamado.

Nota de arquitetura: Perfil e Macroeconômico não são agentes (não exigem
raciocínio — só leem dados já persistidos no DynamoDB via
``gerenciar_perfil_investidor``/``consultar_indicadores_economicos``); o
Orquestrador aciona essas duas tools DIRETAMENTE, sem delegar a um sub-agente
com loop de LLM. Isso reduz a arquitetura a 4 agentes reais: um
Agente_Orquestrador (LLM) e 3 agentes especialistas que ele aciona para o que
exige análise (Risco, Selecao_Ativos, Explicador), cada um também um LLM com
tools restritas à própria política.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agentcore"))
import agentes  # noqa: E402

# Matriz esperada (design.md — "Controle de Acesso por Agente") para os
# agentes especialistas reais (Perfil/Macro são tools diretas do Orquestrador,
# não agentes — ver docstring do módulo).
MATRIZ_ESPERADA = {
    "Agente_Risco": {"calcular_simulacao_risco"},
    "Agente_Selecao_Ativos": {"analisar_portfolio", "selecionar_ativos"},
    "Agente_Explicador": {agentes.TOOL_KB},  # único consumidor do KB
}


def _nomes_tools(nome_agente: str) -> set[str]:
    spec = agentes.AGENTES_ESPECIALISTAS[nome_agente]
    return {t["toolSpec"]["name"] for t in spec["tools"]}


@pytest.mark.property
def test_prop06_tools_por_agente_batem_com_matriz():
    """Cada agente especialista tem exatamente as tools da sua política."""
    for nome, tools_esperadas in MATRIZ_ESPERADA.items():
        assert _nomes_tools(nome) == tools_esperadas, nome


@pytest.mark.property
def test_prop06_sem_vazamento_de_tool_entre_agentes():
    """Nenhuma tool pertence a mais de um agente especialista (isolamento)."""
    vistos: dict[str, str] = {}
    for nome_agente in agentes.AGENTES:
        for tool in _nomes_tools(nome_agente):
            assert tool not in vistos, f"{tool} em {nome_agente} e {vistos.get(tool)}"
            vistos[tool] = nome_agente


@pytest.mark.property
def test_explicador_e_o_unico_consumidor_do_kb():
    """Apenas o Agente_Explicador usa o Knowledge Base (design.md)."""
    usam_kb = [nome for nome in agentes.AGENTES if agentes.TOOL_KB in _nomes_tools(nome)]
    assert usam_kb == ["Agente_Explicador"]


@pytest.mark.property
def test_orquestrador_tem_2_tools_diretas_e_3_meta_tools_de_agente():
    """As ferramentas do Orquestrador são as 2 tools diretas (Perfil/Macro,
    sem raciocínio) + as meta-tools dos 3 agentes especialistas."""
    nomes = {f["toolSpec"]["name"] for f in agentes.FERRAMENTAS_ORQUESTRADOR}
    esperado = agentes._TOOLS_DIRETAS_ORQUESTRADOR | set(agentes._META_PARA_AGENTE.keys())
    assert nomes == esperado
    # As tools diretas não pertencem a nenhum agente especialista (isolamento).
    tools_dos_agentes = {t for nome in agentes.AGENTES for t in _nomes_tools(nome)}
    assert agentes._TOOLS_DIRETAS_ORQUESTRADOR.isdisjoint(tools_dos_agentes)


# ---------------------------------------------------------------------------
# Fakes de integração — simulam a Converse API por script, sem tocar a AWS.
# ---------------------------------------------------------------------------
def _resposta_tool_use(nome_tool: str, entrada: dict[str, Any], id_sufixo: str) -> dict:
    return _resposta_tool_uses([(nome_tool, entrada, id_sufixo)])


def _resposta_tool_uses(chamadas: list[tuple[str, dict[str, Any], str]]) -> dict:
    """Resposta com uma ou mais tool-uses no MESMO turno (chamadas paralelas)."""
    return {
        "output": {
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "toolUse": {
                            "toolUseId": f"t-{sufixo}",
                            "name": nome_tool,
                            "input": entrada,
                        }
                    }
                    for nome_tool, entrada, sufixo in chamadas
                ],
            }
        },
        "stopReason": "tool_use",
    }


def _resposta_texto(texto: str) -> dict:
    return {
        "output": {"message": {"role": "assistant", "content": [{"text": texto}]}},
        "stopReason": "end_turn",
    }


class _ModeloFake:
    """Simula a Converse API: identifica o agente pelo texto do ``sistema`` e
    segue um script fixo de tool-use até encerrar com um texto final.

    O Orquestrador chama, no mesmo turno, as 4 ações independentes entre si
    (as 2 tools diretas + Risco + Selecao_Ativos), e só então o Explicador
    isoladamente — reproduzindo a paralelização real.
    """

    TURNOS_ORQUESTRADOR = (
        (
            ("gerenciar_perfil_investidor", {"operacao": "ler"}, "orq0a"),
            ("consultar_indicadores_economicos", {}, "orq0b"),
            ("consultar_agente_risco", {"pedido": "avaliar risco"}, "orq0c"),
            ("consultar_agente_selecao_ativos", {"pedido": "sugerir ativos"}, "orq0d"),
        ),
        (("consultar_agente_explicador", {"pedido": "explicar ao usuario"}, "orq1"),),
    )

    def __init__(self) -> None:
        self._turnos: dict[str, int] = {}

    def _proximo_turno(self, chave: str) -> int:
        n = self._turnos.get(chave, 0)
        self._turnos[chave] = n + 1
        return n

    def __call__(self, mensagens, ferramentas, sistema, modelo=None):  # noqa: ANN001
        # Checa pelo início do prompt ("Você é o Agente_X"), não por
        # substring solta: os prompts dos especialistas MENCIONAM
        # "Agente_Orquestrador"/"Agente_Explicador" de passagem (nota de
        # concisão), o que faria uma checagem "in sistema" simples confundir
        # o agente. Modelos reais leem o prompt inteiro; só o fake precisa
        # dessa precisão.
        if sistema.startswith("Você é o Agente_Orquestrador"):
            turno = self._proximo_turno("orquestrador")
            if turno < len(self.TURNOS_ORQUESTRADOR):
                return _resposta_tool_uses(list(self.TURNOS_ORQUESTRADOR[turno]))
            return _resposta_texto("Resposta final consolidada pelo Explicador.")
        for nome_agente in agentes.AGENTES:
            if sistema.startswith(f"Você é o {nome_agente}"):
                chave = f"esp:{nome_agente}"
                turno = self._proximo_turno(chave)
                nomes_tools = [f["toolSpec"]["name"] for f in ferramentas]
                if turno < len(nomes_tools):
                    return _resposta_tool_use(
                        nomes_tools[turno], {}, f"{chave}-{turno}"
                    )
                return _resposta_texto(f"Resposta do {nome_agente}.")
        raise AssertionError(f"sistema nao reconhecido: {sistema[:60]!r}")


def _tool_fake(nome_tool: str, argumentos: dict[str, Any]) -> dict[str, Any]:
    if nome_tool == "gerenciar_perfil_investidor":
        return {
            "sucesso": True,
            "perfil": {
                "nome": "Investidor Teste",
                "nivel_conhecimento": "avancado",
                "tolerancia_risco": "MODERADO",
            },
        }
    if nome_tool == "consultar_indicadores_economicos":
        return {"indicadores": [{"indicator_id": "SELIC", "valor": 10.5, "disponivel": True}]}
    if nome_tool == "calcular_simulacao_risco":
        return {"nivel_risco": "MEDIO"}
    return {"ok": True}


def test_orquestrador_aciona_tools_diretas_e_agentes_em_um_unico_turno():
    """Integração: Orquestrador chama as 2 tools diretas (Perfil/Macro, sem
    LLM/agente) e os 2 agentes independentes (Risco/Selecao_Ativos) juntos,
    no mesmo turno; só então aciona o Explicador, isoladamente, por último."""
    modelo = _ModeloFake()
    resultado = agentes.executar_orquestrador(
        {"user_id": "u1", "mensagem": "monte uma recomendação para mim"},
        _tool_fake,
        modelo,
        recuperar_kb=lambda q: ["trecho educativo"],
    )
    # Só os 2 agentes especialistas (Perfil/Macro não entram como "agente").
    assert resultado["agentes_usados"] == [
        "Agente_Risco",
        "Agente_Selecao_Ativos",
        "Agente_Explicador",
    ]
    assert resultado["ferramentas_usadas"] == [
        "gerenciar_perfil_investidor",
        "consultar_indicadores_economicos",
        "calcular_simulacao_risco",
        "analisar_portfolio",
        "selecionar_ativos",
        agentes.TOOL_KB,
    ]
    assert resultado["disclaimer"] == agentes.DISCLAIMER_PROJECOES
    # Atalho de desempenho: a resposta final é a do próprio Agente_Explicador
    # (o Orquestrador não gasta mais uma chamada ao LLM só para repeti-la).
    assert resultado["resposta"] == "Resposta do Agente_Explicador."


def test_orquestrador_pode_chamar_so_a_tool_direta_de_macro():
    """Para um pedido pontual, o Orquestrador pode responder sem acionar
    nenhum agente (ex.: só a tool direta consultar_indicadores_economicos)."""

    class _ModeloSoMacro:
        def __init__(self) -> None:
            self._chamado = False

        def __call__(self, mensagens, ferramentas, sistema, modelo=None):  # noqa: ANN001
            assert sistema.startswith("Você é o Agente_Orquestrador")
            if not self._chamado:
                self._chamado = True
                return _resposta_tool_use(
                    "consultar_indicadores_economicos", {}, "orq0"
                )
            return _resposta_texto("A Selic está em 10,5% a.a.")

    resultado = agentes.executar_orquestrador(
        {"user_id": "u2", "mensagem": "qual a selic hoje?"},
        _tool_fake,
        _ModeloSoMacro(),
    )
    # Nenhum agente especialista foi acionado — só a tool direta.
    assert resultado["agentes_usados"] == []
    assert resultado["ferramentas_usadas"] == ["consultar_indicadores_economicos"]
    assert resultado["resposta"] == "A Selic está em 10,5% a.a."


def test_agentes_usam_o_modelo_correto():
    """Custo/latência: Risco e Selecao_Ativos usam o modelo rápido (Haiku);
    Explicador usa o mesmo modelo do Orquestrador (Opus) — só ele redige a
    resposta final que o usuário lê."""
    assert agentes.AGENTES_ESPECIALISTAS["Agente_Risco"]["modelo"] == agentes.MODELO_LLM_RAPIDO
    assert (
        agentes.AGENTES_ESPECIALISTAS["Agente_Selecao_Ativos"]["modelo"]
        == agentes.MODELO_LLM_RAPIDO
    )
    assert agentes.AGENTES_ESPECIALISTAS["Agente_Explicador"]["modelo"] == agentes.MODELO_LLM
    assert agentes.MODELO_LLM_RAPIDO != agentes.MODELO_LLM


def test_loop_tool_use_propaga_o_modelo_escolhido_para_chamar_modelo():
    """O modelo passado a ``_loop_tool_use`` chega de fato ao ``chamar_modelo``
    (regressão: garante que Risco/Selecao_Ativos realmente usam o Haiku)."""
    modelos_recebidos: list[str] = []

    def chamar_modelo_fake(mensagens, ferramentas, sistema, modelo=agentes.MODELO_LLM):
        modelos_recebidos.append(modelo)
        return _resposta_texto("ok")

    agentes._loop_tool_use(
        [{"role": "user", "content": [{"text": "oi"}]}],
        [],
        "sistema qualquer",
        chamar_modelo_fake,
        lambda nome, entrada: {},
        max_iteracoes=1,
        modelo=agentes.MODELO_LLM_RAPIDO,
    )
    assert modelos_recebidos == [agentes.MODELO_LLM_RAPIDO]


def test_atalho_pula_round_trip_final_apos_o_explicador():
    """Desempenho: ao acionar o Agente_Explicador, o Orquestrador usa a
    resposta dele diretamente como final, sem gastar mais uma chamada ao LLM
    (o passo mais lento do pipeline) só para "confirmar" o mesmo texto."""

    chamadas_orquestrador = {"n": 0}

    class _ModeloSoExplicador:
        def __call__(self, mensagens, ferramentas, sistema, modelo=None):  # noqa: ANN001
            if sistema.startswith("Você é o Agente_Orquestrador"):
                chamadas_orquestrador["n"] += 1
                if chamadas_orquestrador["n"] == 1:
                    return _resposta_tool_use(
                        "consultar_agente_explicador",
                        {"pedido": "explique ao usuario"},
                        "orq0",
                    )
                # Não deveria ser chamado de novo após o atalho — se for, o
                # teste abaixo (contagem de chamadas) falha primeiro.
                return _resposta_texto("NAO DEVERIA APARECER")
            assert sistema.startswith("Você é o Agente_Explicador")
            return _resposta_texto("Resposta final do Explicador, direto.")

    resultado = agentes.executar_orquestrador(
        {"user_id": "u3", "mensagem": "explique meu investimento"},
        _tool_fake,
        _ModeloSoExplicador(),
    )
    assert resultado["resposta"] == "Resposta final do Explicador, direto."
    assert resultado["agentes_usados"] == ["Agente_Explicador"]
    # Só 1 chamada ao Orquestrador (a que pediu o Explicador); nenhuma
    # segunda chamada para "confirmar" o texto final.
    assert chamadas_orquestrador["n"] == 1


# ---------------------------------------------------------------------------
# Regressão: dados de carteira citados na saudação/memória sem consultar a
# tool real (bug relatado — ver docs/decisoes-agentes-e-latencia.md). Uma
# instrução textual "obrigatória" no prompt foi testada isoladamente contra o
# Runtime real e o modelo a ignorou duas vezes seguidas; a correção definitiva
# usa ``toolChoice`` da Converse API para forçar Agente_Selecao_Ativos no
# primeiro turno quando a heurística de palavra-chave detecta o pedido.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "mensagem, esperado",
    [
        ("qual o valor total da minha carteira hoje?", True),
        ("como minha carteira está composta?", True),
        ("quanto tenho investido em ações?", True),
        ("qual é o meu patrimônio atual?", True),
        ("me fale sobre meus ativos", True),
        ("oi, como vai?", False),
        ("qual a taxa Selic hoje?", False),
        ("me explique o que é um FII", False),
    ],
)
def test_mensagem_pede_dados_de_carteira(mensagem: str, esperado: bool):
    assert agentes._mensagem_pede_dados_de_carteira(mensagem) is esperado


def test_sistema_com_memoria_adiciona_aviso_so_quando_pede_carteira():
    base = "BASE"
    sem_pedido = agentes._sistema_com_memoria(base, ["fato x"], "oi, como vai?")
    com_pedido = agentes._sistema_com_memoria(
        base, ["fato x"], "qual o valor da minha carteira?"
    )
    assert "AVISO PARA ESTA MENSAGEM" not in sem_pedido
    assert "AVISO PARA ESTA MENSAGEM" in com_pedido


def test_orquestrador_forca_tool_choice_quando_pergunta_e_sobre_carteira():
    """Ao perguntar pelo valor/composição da carteira, o Orquestrador deve
    forçar consultar_agente_selecao_ativos no primeiro turno (toolChoice),
    não confiar em o modelo "decidir" chamar por instrução textual."""

    chamadas: list[dict[str, Any] | None] = []

    class _ModeloRegistraToolChoice:
        def __call__(self, mensagens, ferramentas, sistema, modelo=None, tool_choice=None):  # noqa: ANN001
            chamadas.append(tool_choice)
            if len(chamadas) == 1:
                return _resposta_tool_use(
                    "consultar_agente_selecao_ativos",
                    {"pedido": "analisar portfolio"},
                    "orq0",
                )
            return _resposta_texto("Sua carteira vale R$ 18.000.")

    resultado = agentes.executar_orquestrador(
        {"user_id": "u9", "mensagem": "qual o valor total da minha carteira?"},
        _tool_fake,
        _ModeloRegistraToolChoice(),
    )
    assert resultado["agentes_usados"] == ["Agente_Selecao_Ativos"]
    # Primeiro turno: tool forçada. Turnos seguintes: escolha livre (auto).
    assert chamadas[0] == {"tool": {"name": "consultar_agente_selecao_ativos"}}
    assert all(tc is None for tc in chamadas[1:])


def test_orquestrador_nao_forca_tool_choice_em_pergunta_generica():
    """Sem menção à carteira, o Orquestrador decide livremente (sem
    toolChoice forçado) — a heurística não deve interferir em pedidos que não
    são sobre dados de carteira."""

    chamadas: list[dict[str, Any] | None] = []

    class _ModeloRegistraToolChoice:
        def __call__(self, mensagens, ferramentas, sistema, modelo=None, tool_choice=None):  # noqa: ANN001
            chamadas.append(tool_choice)
            return _resposta_texto("Olá! Como posso ajudar?")

    agentes.executar_orquestrador(
        {"user_id": "u10", "mensagem": "oi, como vai?"},
        _tool_fake,
        _ModeloRegistraToolChoice(),
    )
    assert chamadas == [None]


def test_loop_tool_use_aceita_chamador_sem_suporte_a_tool_choice():
    """Compatibilidade: quando nenhuma tool é forçada (caso comum), o loop
    chama o modelo sem o kwarg tool_choice — chamadores/mocks que só aceitam
    a assinatura antiga (sem tool_choice) continuam funcionando."""

    class _ModeloAntigoSemToolChoice:
        def __call__(self, mensagens, ferramentas, sistema, modelo=None):  # noqa: ANN001
            return _resposta_texto("ok")

    texto, usados = agentes._loop_tool_use(
        [{"role": "user", "content": [{"text": "oi"}]}],
        [],
        "sistema",
        _ModeloAntigoSemToolChoice(),
        lambda nome, entrada: {},
        3,
    )
    assert texto == "ok"
    assert usados == []
