"""Agentes do GEVI (AgentCore Runtime) — Orquestrador + especialistas.

Arquitetura de 4 agentes reais: um Agente_Orquestrador (LLM, tool-use) que
decide quais dos 3 agentes especialistas acionar para atender o pedido do
investidor. Cada especialista é, ele mesmo, um agente LLM com seu próprio loop
de tool-use, mas com acesso APENAS às ferramentas da sua política (isolamento
— Propriedade 6 do design):

- Agente_Risco             → calcular_simulacao_risco
- Agente_Selecao_Ativos    → analisar_portfolio, selecionar_ativos
- Agente_Explicador (XAI)  → Knowledge Base (RAG) — único consumidor do KB

Perfil e Macroeconômico NÃO são agentes (não exigem raciocínio, só leem dados
já persistidos no DynamoDB): o Orquestrador aciona ``gerenciar_perfil_investidor``
e ``consultar_indicadores_economicos`` como tools DIRETAS do Gateway, sem
delegar a um sub-agente — economiza o loop de tool-use inteiro (2+ chamadas ao
LLM) que um agente dedicado exigiria, sem perda de qualidade, já que essas
duas tools apenas relatam o estado atual.

O Orquestrador não aciona as demais tools do Gateway diretamente: para as
ações que exigem análise (risco, seleção de ativos, explicação), suas
"ferramentas" são os próprios agentes especialistas (uma meta-tool por
agente, ex.: ``consultar_agente_risco``). Ao "chamar" um agente, o
Orquestrador descreve a tarefa em linguagem natural (campo ``pedido``); o
especialista executa seu loop de tool-use e devolve uma resposta em texto,
que volta ao Orquestrador como resultado da tool. O Agente_Explicador deve
ser o último acionado quando a resposta final é apresentada ao usuário — ele
consolida os achados dos demais e adapta a linguagem ao nível de conhecimento
do investidor.

Segurança: o ``user_id`` NUNCA vem do modelo — é injetado pelo executor de
ferramentas a partir do JWT autenticado (ver ``servidor.py``). Nenhum agente
(orquestrador ou especialista) recebe o ``user_id`` no schema das ferramentas,
evitando que o modelo o invente ou peça.

Dependências injetadas (mantém o módulo testável, sem AWS):
- ``chamar_tool(nome, argumentos) -> dict``: invoca uma tool via Gateway (MCP).
- ``chamar_modelo(mensagens, ferramentas, sistema) -> dict``: chama a Converse API.
- ``recuperar_kb(consulta) -> list[str]``: RAG do Knowledge Base (usado só pelo
  Agente_Explicador).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Protocol

# Modelos fixados no código (decisão do projeto — invocados via inference
# profile). O Opus só é usado onde a qualidade de raciocínio/redação importa
# de fato: o Agente_Orquestrador (decide o que fazer) e o Agente_Explicador
# (escreve a resposta final que o usuário lê). Os demais agentes só formatam
# notas internas concisas a partir do retorno de uma tool — usam o Haiku
# (bem mais rápido) sem perda de qualidade percebida pelo usuário.
MODELO_LLM = "us.anthropic.claude-opus-4-5-20251101-v1:0"
MODELO_LLM_RAPIDO = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

# Teto de iterações de cada loop de tool-use. O Orquestrador tende a precisar
# de mais turnos (um por agente acionado); os especialistas normalmente
# resolvem em 1-2 chamadas de tool.
MAX_ITERACOES_ORQUESTRADOR = 8
MAX_ITERACOES_ESPECIALISTA = 4

DISCLAIMER_PROJECOES = (
    "Projeções baseadas em dados históricos e não garantem resultados futuros."
)

# Tool de RAG (não é uma tool do Gateway; resolvida localmente via recuperar_kb).
# Exclusiva do Agente_Explicador (isolamento — Propriedade 6).
TOOL_KB = "consultar_base_conhecimento"


# ---------------------------------------------------------------------------
# Ferramentas reais (Gateway/KB), uma por Lambda/capacidade — cada uma vai para
# a política de exatamente um agente especialista (matriz do design.md).
# ---------------------------------------------------------------------------
FERRAMENTA_PERFIL: dict[str, Any] = {
    "toolSpec": {
        "name": "gerenciar_perfil_investidor",
        "description": (
            "Lê ou gerencia o perfil do investidor autenticado (tolerância a "
            "risco e nível de conhecimento). Use operacao='ler' para obter o "
            "perfil antes de recomendar."
        ),
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "operacao": {
                        "type": "string",
                        "enum": ["criar", "ler", "atualizar", "remover"],
                        "description": "Operação no perfil (normalmente 'ler').",
                    }
                },
                "required": ["operacao"],
            }
        },
    }
}

FERRAMENTA_MACRO: dict[str, Any] = {
    "toolSpec": {
        "name": "consultar_indicadores_economicos",
        "description": (
            "Retorna os indicadores macroeconômicos mais recentes (Selic, "
            "IPCA, dólar, CDI) e suas variações."
        ),
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "indicadores": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Lista opcional (padrão: todos).",
                    }
                },
            }
        },
    }
}

FERRAMENTA_RISCO: dict[str, Any] = {
    "toolSpec": {
        "name": "calcular_simulacao_risco",
        "description": (
            "Calcula volatilidade, classificação de risco e três cenários "
            "(pessimista/realista/otimista) para um investimento."
        ),
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "valor_inicial": {"type": "number"},
                    "prazo_meses": {"type": "integer"},
                    "tipo_ativo": {
                        "type": "string",
                        "enum": ["RENDA_FIXA", "RENDA_VARIAVEL", "FII", "CRIPTO"],
                    },
                },
                "required": ["valor_inicial", "prazo_meses", "tipo_ativo"],
            }
        },
    }
}

FERRAMENTA_ANALISE_PORTFOLIO: dict[str, Any] = {
    "toolSpec": {
        "name": "analisar_portfolio",
        "description": (
            "Analisa a composição e diversificação do portfólio do usuário e "
            "aponta concentrações acima de 30%."
        ),
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "tolerancia_risco": {
                        "type": "string",
                        "enum": ["CONSERVADOR", "MODERADO", "ARROJADO"],
                    }
                },
            }
        },
    }
}

FERRAMENTA_SELECAO_ATIVOS: dict[str, Any] = {
    "toolSpec": {
        "name": "selecionar_ativos",
        "description": (
            "Sugere e ranqueia ativos compatíveis com o perfil por relação "
            "risco/retorno. Com operacao='consultar_historico', retorna o "
            "histórico de recomendações e simulações do usuário."
        ),
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "tolerancia_risco": {
                        "type": "string",
                        "enum": ["CONSERVADOR", "MODERADO", "ARROJADO"],
                    },
                    "operacao": {
                        "type": "string",
                        "description": "Opcional: 'consultar_historico'.",
                    },
                },
            }
        },
    }
}

FERRAMENTA_KB: dict[str, Any] = {
    "toolSpec": {
        "name": TOOL_KB,
        "description": (
            "Consulta a base de conhecimento de educação financeira (RAG). "
            "Use para explicar conceitos a investidores iniciantes."
        ),
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {"consulta": {"type": "string"}},
                "required": ["consulta"],
            }
        },
    }
}


# ---------------------------------------------------------------------------
# Prompts e política de tools de cada agente especialista (design.md —
# "Agentes Inteligentes" e "Controle de Acesso por Agente").
# ---------------------------------------------------------------------------
_NOTA_CONCISAO = (
    "IMPORTANTE (desempenho): sua resposta é uma NOTA INTERNA para o "
    "Agente_Orquestrador/Agente_Explicador, não o texto final ao usuário — "
    "eles cuidam da formatação e da linguagem final. Seja telegráfico: liste "
    "só os números e fatos relevantes, em texto corrido ou tópicos curtos, "
    "SEM markdown decorado (sem títulos, tabelas, emojis) e SEM frases de "
    "cortesia. Isso reduz bastante a latência."
)

SISTEMA_AGENTE_RISCO = (
    "Você é o Agente_Risco do GEVI. Sua responsabilidade é calcular "
    "volatilidade, avaliar exposição financeira e classificar o risco de um "
    "investimento com base nos indicadores econômicos atualizados.\n"
    "- Use calcular_simulacao_risco com os parâmetros informados na tarefa "
    "(valor_inicial, prazo_meses, tipo_ativo). Se algum parâmetro não vier "
    "explícito, use uma estimativa razoável e informe claramente que foi "
    "assumida.\n"
    "- Reporte a classificação de risco e os três cenários (pessimista/"
    "realista/otimista) retornados pela ferramenta, sem inventar números.\n"
    f"- {_NOTA_CONCISAO}"
)

SISTEMA_AGENTE_SELECAO_ATIVOS = (
    "Você é o Agente_Selecao_Ativos do GEVI. Sua responsabilidade é analisar "
    "a composição do portfólio do investidor e gerar sugestões de ativos "
    "compatíveis com o perfil de risco informado na tarefa.\n"
    "- Use analisar_portfolio para entender a composição e diversificação "
    "atual (destaque concentração acima de 30%).\n"
    "- Use selecionar_ativos (com a tolerancia_risco informada) para "
    "ranquear sugestões por relação risco/retorno; use "
    "operacao='consultar_historico' somente se a tarefa pedir o histórico de "
    "recomendações.\n"
    "- Ao chamar as ferramentas, peça analisar_portfolio e "
    "selecionar_ativos no MESMO turno (uma tool call de cada, juntas) — "
    "elas são independentes entre si e rodam em paralelo, o que acelera a "
    "resposta. NUNCA chame uma, espere o resultado e só depois decida "
    "chamar a outra.\n"
    "- Baseie-se apenas nos dados retornados pelas ferramentas. Use o "
    "assetId de cada ativo EXATAMENTE como veio de analisar_portfolio (ex.: "
    "se o assetId é 'Tijolos', escreva 'Tijolos' — NUNCA reformule, "
    "expanda ou 'corrija' o nome do ativo, mesmo que pareça abreviado ou "
    "diferente do que você lembra de conversas anteriores; o nome exato é "
    "o que o investidor cadastrou).\n"
    f"- {_NOTA_CONCISAO}"
)

SISTEMA_AGENTE_EXPLICADOR = (
    "Você é o Agente_Explicador (XAI) do GEVI, o último a atuar. Sua "
    "responsabilidade é consolidar os achados dos demais agentes (fornecidos "
    "na tarefa) e traduzi-los em uma resposta final ao investidor, em "
    "português, adaptada ao nível de conhecimento informado.\n"
    "- Nível 'basico': linguagem simples, evite jargão, e use "
    "consultar_base_conhecimento para trazer conteúdo educativo "
    "complementar sobre os 2-3 conceitos MAIS importantes citados (não é "
    "necessário buscar todo conceito mencionado).\n"
    "- Nível 'avancado': linguagem técnica e objetiva, sem explicações "
    "básicas nem consultas ao KB, salvo se a tarefa pedir explicitamente.\n"
    "- IMPORTANTE (desempenho): se precisar consultar mais de um conceito no "
    "KB, faça TODAS as chamadas de consultar_base_conhecimento no MESMO "
    "turno (uma tool call por conceito, todas de uma vez) — elas rodam em "
    "paralelo. NUNCA consulte um conceito, espere o resultado e só depois "
    "decida consultar o próximo; isso é lento. Decida de antemão quais "
    "conceitos precisa e peça todos juntos.\n"
    f"- Se a resposta envolver projeções ou simulações, inclua: "
    f"'{DISCLAIMER_PROJECOES}'\n"
    "- Baseie-se somente nos dados fornecidos na tarefa; não invente "
    "números NEM nomes de ativos. Se a tarefa trouxer um assetId (ex.: "
    "'Tijolos', 'PETR4'), reproduza-o EXATAMENTE como veio — nunca "
    "reformule, expanda ou substitua pelo nome que você lembra de "
    "conversas anteriores (a memória pode estar desatualizada; o dado da "
    "tarefa é sempre o atual). Esta é a resposta final apresentada ao "
    "usuário — escreva de forma clara, completa e bem organizada."
)

# {nome_do_agente: {"tools": [...], "sistema": "..."}}. Cada agente só recebe
# as ferramentas da própria política — isolamento real (Propriedade 6).
# Perfil e Macroeconômico não estão aqui: são tools diretas do Orquestrador
# (ver FERRAMENTAS_ORQUESTRADOR), não agentes — não exigem raciocínio.
#
# ``modelo``: Risco e Selecao_Ativos só produzem notas internas concisas (sem
# formatação para o usuário) — usam MODELO_LLM_RAPIDO (Haiku). O Explicador
# escreve a resposta final que o usuário lê — usa MODELO_LLM (Opus), igual ao
# Orquestrador.
AGENTES_ESPECIALISTAS: dict[str, dict[str, Any]] = {
    "Agente_Risco": {
        "tools": [FERRAMENTA_RISCO],
        "sistema": SISTEMA_AGENTE_RISCO,
        "modelo": MODELO_LLM_RAPIDO,
    },
    "Agente_Selecao_Ativos": {
        "tools": [FERRAMENTA_ANALISE_PORTFOLIO, FERRAMENTA_SELECAO_ATIVOS],
        "sistema": SISTEMA_AGENTE_SELECAO_ATIVOS,
        "modelo": MODELO_LLM_RAPIDO,
    },
    "Agente_Explicador": {
        "tools": [FERRAMENTA_KB],
        "sistema": SISTEMA_AGENTE_EXPLICADOR,
        "modelo": MODELO_LLM,
    },
}

# Ordem canônica dos 3 especialistas (design.md).
AGENTES: tuple[str, ...] = tuple(AGENTES_ESPECIALISTAS.keys())


# ---------------------------------------------------------------------------
# Agente_Orquestrador — combina tools DIRETAS do Gateway (Perfil, Macro — só
# leitura, sem raciocínio) com meta-tools que acionam os agentes especialistas
# (Risco, Selecao_Ativos, Explicador — exigem análise). Decide quais acionar
# e em que ordem.
# ---------------------------------------------------------------------------
def _meta_tool_agente(nome_tool: str, agente: str, descricao: str) -> dict[str, Any]:
    return {
        "toolSpec": {
            "name": nome_tool,
            "description": descricao,
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "pedido": {
                            "type": "string",
                            "description": (
                                f"O que pedir ao {agente}, em linguagem natural. "
                                "Inclua achados relevantes já obtidos de outros "
                                "agentes, quando fizer diferença para a tarefa."
                            ),
                        }
                    },
                    "required": ["pedido"],
                }
            },
        }
    }


FERRAMENTA_ORQ_RISCO = _meta_tool_agente(
    "consultar_agente_risco",
    "Agente_Risco",
    "Aciona o Agente_Risco para calcular volatilidade, cenários (pessimista/"
    "realista/otimista) e classificar o risco de um investimento. Inclua no "
    "pedido valor, prazo e tipo de ativo, quando conhecidos.",
)
FERRAMENTA_ORQ_SELECAO = _meta_tool_agente(
    "consultar_agente_selecao_ativos",
    "Agente_Selecao_Ativos",
    "Aciona o Agente_Selecao_Ativos para analisar o portfólio atual do "
    "investidor e sugerir/ranquear ativos compatíveis com o perfil. Inclua "
    "a tolerância a risco no pedido, se já souber.",
)
FERRAMENTA_ORQ_EXPLICADOR = _meta_tool_agente(
    "consultar_agente_explicador",
    "Agente_Explicador",
    "Aciona o Agente_Explicador (XAI), que deve ser chamado por ÚLTIMO, para "
    "consolidar os achados dos demais agentes e produzir a resposta final "
    "adaptada ao nível de conhecimento do investidor (básico ou avançado), "
    "incluindo conteúdo educativo quando necessário. Inclua no pedido um "
    "resumo dos achados dos outros agentes, o nível de conhecimento do "
    "investidor e a pergunta original.",
)

# Tools DIRETAS do Gateway acionadas pelo próprio Orquestrador (sem
# delegar a um agente): Perfil e Macro só leem dados já persistidos, não
# exigem raciocínio de um sub-agente.
FERRAMENTAS_ORQUESTRADOR: list[dict[str, Any]] = [
    FERRAMENTA_PERFIL,
    FERRAMENTA_MACRO,
    FERRAMENTA_ORQ_RISCO,
    FERRAMENTA_ORQ_SELECAO,
    FERRAMENTA_ORQ_EXPLICADOR,
]

# Nomes das tools diretas (Gateway) que o Orquestrador pode chamar sem
# delegar a um agente — usado para diferenciar do caminho das meta-tools.
_TOOLS_DIRETAS_ORQUESTRADOR = frozenset(
    {"gerenciar_perfil_investidor", "consultar_indicadores_economicos"}
)

# meta-tool do Orquestrador -> agente especialista que ela aciona.
_META_PARA_AGENTE: dict[str, str] = {
    "consultar_agente_risco": "Agente_Risco",
    "consultar_agente_selecao_ativos": "Agente_Selecao_Ativos",
    "consultar_agente_explicador": "Agente_Explicador",
}

SISTEMA_ORQUESTRADOR = (
    "Você é o Agente_Orquestrador do GEVI, um assistente multiagente de "
    "investimentos. Você tem duas ferramentas de leitura DIRETA (sem "
    "delegar a um agente, pois não exigem análise):\n"
    "- gerenciar_perfil_investidor: perfil de risco e nível de conhecimento "
    "do investidor (use operacao='ler').\n"
    "- consultar_indicadores_economicos: cenário econômico atual (Selic, "
    "IPCA, dólar, CDI).\n\n"
    "Para o que exige análise, você coordena três agentes especialistas, "
    "cada um com acesso apenas às próprias ferramentas:\n"
    "- Agente_Risco: volatilidade, classificação de risco e cenários de "
    "simulação.\n"
    "- Agente_Selecao_Ativos: análise do portfólio e ranqueamento de ativos.\n"
    "- Agente_Explicador (XAI): consolida os achados e produz a resposta "
    "final adaptada ao nível de conhecimento do investidor; deve ser o "
    "ÚLTIMO agente chamado sempre que a resposta final for apresentada ao "
    "usuário.\n\n"
    "Regras:\n"
    "- REGRA OBRIGATÓRIA (sem exceção): se o usuário pedir, de qualquer "
    "forma, o valor, a composição, os ativos ou o percentual da carteira "
    "dele (ex.: 'qual o valor da minha carteira', 'como ela está "
    "composta', 'quanto tenho investido'), você é PROIBIDO de responder "
    "com números OU nomes de ativos vindos da memória de longo prazo ou do "
    "histórico da conversa. Você DEVE chamar Agente_Selecao_Ativos (que "
    "executa analisar_portfolio) NESTE turno, antes de responder, e usar "
    "apenas os valores e os assetId (nome exato de cada ativo) retornados "
    "por ele, sem reformular. A memória de longo prazo é sempre "
    "desatualizada para esse tipo de dado — mesmo que pareça recente ou "
    "específica, mesmo que você já tenha respondido isso antes na mesma "
    "conversa, mesmo que o nome do ativo pareça abreviado ou diferente do "
    "que a memória registrou (ex.: memória diz 'FIIs de Tijolo', mas a "
    "tool retornou 'Tijolos' — use 'Tijolos', o dado da tool está sempre "
    "certo). Frases como 'com base no que já sei' ou 'já tenho essa "
    "informação' NÃO justificam pular a tool call quando o pedido é sobre "
    "a carteira.\n"
    "- O usuário já está autenticado; o identificador dele é injetado "
    "automaticamente nas ferramentas e agentes. NUNCA peça nem invente o "
    "user_id.\n"
    "- Decida QUAIS ferramentas/agentes chamar de acordo com o pedido — não "
    "é obrigatório chamar todos.\n"
    "- IMPORTANTE (desempenho): você pode chamar MAIS DE UMA ferramenta/"
    "agente no MESMO turno (várias tool calls de uma vez) sempre que forem "
    "independentes entre si — elas são executadas em paralelo, o que "
    "acelera bastante a resposta. gerenciar_perfil_investidor, "
    "consultar_indicadores_economicos, Agente_Risco e Agente_Selecao_Ativos "
    "são TODOS independentes entre si (cada um resolve o que precisa a "
    "partir do usuário autenticado, sem depender da resposta dos outros) — "
    "para uma recomendação completa, chame os 4 JUNTOS, no MESMO turno. NÃO "
    "espere um responder para chamar o próximo. Só depois, com os 4 "
    "resultados em mãos, chame o Agente_Explicador isoladamente, por "
    "último, para consolidar tudo.\n"
    "- Ao chamar um agente (Risco/Selecao_Ativos/Explicador), descreva no "
    "campo 'pedido' o que ele deve fazer, incluindo (quando relevante) os "
    "achados já obtidos de outras ferramentas/agentes, para que trabalhe "
    "com o contexto necessário.\n"
    "- Ao chamar o Agente_Explicador, inclua no pedido um resumo dos "
    "achados coletados e o nível de conhecimento do investidor (obtido de "
    "gerenciar_perfil_investidor).\n"
    "- Depois de chamar o Agente_Explicador, responda ao usuário com o "
    "texto produzido por ele (é a resposta final, já adaptada), sem reduzi-"
    "lo.\n"
    "- Para pedidos simples que não exigem consolidação (ex.: uma pergunta "
    "pontual respondida só com o perfil ou só com os indicadores), você "
    "pode responder direto com o dado obtido, sem precisar chamar o "
    "Explicador.\n"
    "- Você TEM memória: lembra do histórico desta conversa e do que "
    "aprendeu sobre o investidor em conversas anteriores (preferências e "
    "fatos). Use esse contexto naturalmente. NUNCA diga que não é capaz de "
    "lembrar, anotar ou armazenar informações — se o usuário pedir para "
    "anotar algo, confirme que vai lembrar.\n"
    "- CUIDADO com a memória de longo prazo: ela é útil para preferências e "
    "contexto (ex.: 'gosta de FIIs', 'já perguntou sobre X'), mas NÃO é "
    "fonte confiável para dados de identidade (nome, e-mail, data de "
    "nascimento) nem para valores/ativos da carteira (ver a REGRA "
    "OBRIGATÓRIA acima). Se o usuário perguntar seu próprio nome ou dados "
    "cadastrais, chame gerenciar_perfil_investidor para confirmar, em vez "
    "de responder só com o que está na memória. Em uma saudação informal "
    "(ex.: só 'oi', sem pedir a carteira), pode mencionar o perfil de "
    "risco e o comportamento do investidor em termos gerais, mas NÃO cite "
    "valores, ativos específicos ou patrimônio numérico — isso só com a "
    "tool chamada.\n"
    "- NUNCA afirme que uma informação 'foi obtida do cadastro' ou 'da sua "
    "carteira atual' se você não consultou a ferramenta correspondente de "
    "fato — isso seria uma explicação inventada (viola XAI). Se notar uma "
    "memória que pareça claramente errada, desatualizada ou inconsistente "
    "com um dado real que você consultou, prefira o dado real e não repita "
    "a memória suspeita como fato.\n"
    "- Responda em português, de forma clara e organizada."
)


class ChamadorTool(Protocol):
    def __call__(self, nome_tool: str, argumentos: dict[str, Any]) -> dict[str, Any]:
        ...


class ChamadorModelo(Protocol):
    def __call__(
        self,
        mensagens: list[dict[str, Any]],
        ferramentas: list[dict[str, Any]],
        sistema: str,
        modelo: str = MODELO_LLM,
        tool_choice: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ...


RecuperadorKB = Callable[[str], list[str]]


def _texto_da_mensagem(mensagem: dict[str, Any]) -> str:
    """Concatena os blocos de texto de uma mensagem da Converse API."""
    partes = [b["text"] for b in mensagem.get("content", []) if "text" in b]
    return "\n".join(p for p in partes if p).strip()


def _mensagens_do_historico(historico: list[Any]) -> list[dict[str, Any]]:
    """Converte turnos ``(papel, texto)`` da memória em mensagens da Converse API.

    Garante o contrato da Converse: a lista começa com ``user`` e alterna os
    papéis (descarta um turno que repita o papel do anterior). Assim o histórico
    da AgentCore Memory pode ser semeado como contexto sem quebrar a chamada.
    """
    mensagens: list[dict[str, Any]] = []
    for turno in historico or []:
        try:
            papel, texto = turno
        except (ValueError, TypeError):
            continue
        texto = str(texto or "").strip()
        if not texto:
            continue
        role = "assistant" if str(papel).upper() == "ASSISTANT" else "user"
        # Precisa começar com user; descarta assistants iniciais.
        if not mensagens and role != "user":
            continue
        # Impede papéis repetidos consecutivos (Converse exige alternância).
        if mensagens and mensagens[-1]["role"] == role:
            continue
        mensagens.append({"role": role, "content": [{"text": texto}]})
    # A mensagem atual do usuário será anexada depois; o histórico deve terminar
    # em ``assistant`` (ou vazio) para preservar a alternância user/assistant e
    # não gerar dois ``user`` seguidos (a Converse rejeita papéis repetidos).
    if mensagens and mensagens[-1]["role"] == "user":
        mensagens.pop()
    return mensagens


_PALAVRAS_CARTEIRA = (
    "carteira",
    "portfolio",
    "portfólio",
    "meus ativos",
    "meus investimentos",
    "quanto tenho investido",
    "patrimonio",
    "patrimônio",
    "meu dinheiro investido",
)


def _mensagem_pede_dados_de_carteira(mensagem: str) -> bool:
    """Heurística por palavra-chave: a mensagem pede valor/composição real da
    carteira do investidor (não apenas uma menção de passagem)?

    Instruções textuais de "chame a tool antes de responder sobre a carteira"
    já foram testadas isoladamente no prompt e o modelo ignorou (respondeu da
    memória de longo prazo sem chamar Agente_Selecao_Ativos, mesmo com a regra
    marcada como obrigatória — ver docs/decisoes-agentes-e-latencia.md). Por
    isso a detecção é feita em código: quando bate, um aviso extra e isolado é
    anexado ao fim do system prompt (mais peso que uma regra no meio de um
    bloco longo) especificamente para este turno.
    """
    texto = str(mensagem or "").lower()
    return any(p in texto for p in _PALAVRAS_CARTEIRA)


def _sistema_com_memoria(
    base: str, memorias: list[str], mensagem_atual: str = ""
) -> str:
    """Anexa ao ``base`` (system prompt) o que a memória de longo prazo já sabe.

    Quando a mensagem atual pede dados da carteira, anexa também um aviso
    isolado e específico deste turno reforçando que os números devem vir da
    tool, não da memória (ver ``_mensagem_pede_dados_de_carteira``).
    """
    itens = [str(m).strip() for m in (memorias or []) if str(m).strip()]
    resultado = base
    if itens:
        bloco = "\n".join(f"- {m}" for m in itens)
        resultado = (
            f"{resultado}\n\n"
            "Memória de longo prazo — o que você já sabe sobre este investidor "
            "(de conversas anteriores; use quando for relevante, sem repetir de "
            f"forma literal):\n{bloco}"
        )
    if _mensagem_pede_dados_de_carteira(mensagem_atual):
        resultado = (
            f"{resultado}\n\n"
            "⚠️ AVISO PARA ESTA MENSAGEM: o investidor está pedindo dados da "
            "carteira (valor, composição, ativos ou patrimônio). Você AINDA "
            "NÃO tem os números atuais — o que está na memória de longo "
            "prazo acima (se houver) é de uma conversa passada e pode estar "
            "desatualizado. Chame Agente_Selecao_Ativos AGORA, neste turno, "
            "e responda só com os números que ele retornar. Não responda "
            "esta mensagem específica usando os valores da memória."
        )
    return resultado


def _executar_ferramenta(
    nome: str,
    entrada: dict[str, Any],
    user_id: str | None,
    chamar_tool: ChamadorTool,
    recuperar_kb: RecuperadorKB | None,
) -> dict[str, Any]:
    """Executa uma ferramenta real (Gateway ou KB), injetando o user_id do JWT."""
    argumentos = dict(entrada or {})
    if nome == TOOL_KB:
        consulta = argumentos.get("consulta", "")
        trechos = recuperar_kb(consulta) if recuperar_kb else []
        return {"trechos": trechos}
    # Ferramentas do Gateway: user_id é sempre o do usuário autenticado.
    if user_id:
        argumentos["user_id"] = user_id
    return chamar_tool(nome, argumentos)


def _executar_usos_em_paralelo(
    usos: list[dict[str, Any]],
    executor: Callable[[str, dict[str, Any]], dict[str, Any]],
) -> list[tuple[dict[str, Any], Any]]:
    """Executa as tool-uses de um mesmo turno em paralelo (threads).

    Cada chamada é uma requisição de rede (Gateway/Bedrock), então paralelizar
    reduz bastante a latência quando o modelo pede várias ferramentas
    independentes no mesmo turno (ex.: o Orquestrador acionando vários agentes
    especialistas de uma vez). Exceções de uma chamada não cancelam as demais
    — voltam ao modelo como ``toolResult`` de status ``error``.

    Retorna uma lista de ``(mensagem_toolResult, valor_bruto_do_executor)`` na
    MESMA ORDEM de ``usos`` (``ThreadPoolExecutor.map`` preserva a ordem de
    submissão no resultado, independente de qual thread termina primeiro).
    Isso permite que o chamador faça bookkeeping determinístico (ex.: registrar
    quais agentes/ferramentas foram usados, na ordem em que o modelo pediu)
    mesmo com a execução ocorrendo em paralelo — qualquer efeito colateral
    dentro de ``executor`` aconteceria em ordem de conclusão das threads, não
    de submissão, por isso não deve ser usado para esse fim; use o valor
    retornado aqui.
    """

    def _rodar(uso: dict[str, Any]) -> tuple[dict[str, Any], Any]:
        try:
            valor = executor(uso["name"], uso.get("input", {}))
            conteudo = [{"json": valor}]
            status = "success"
        except Exception as exc:  # noqa: BLE001 - devolve erro ao modelo
            conteudo = [{"text": f"Erro ao executar a ferramenta: {exc}"}]
            status = "error"
            valor = None
        msg = {
            "toolResult": {
                "toolUseId": uso["toolUseId"],
                "content": conteudo,
                "status": status,
            }
        }
        return msg, valor

    if len(usos) <= 1:
        return [_rodar(uso) for uso in usos]
    with ThreadPoolExecutor(max_workers=len(usos)) as pool:
        return list(pool.map(_rodar, usos))


def _loop_tool_use(
    mensagens: list[dict[str, Any]],
    ferramentas: list[dict[str, Any]],
    sistema: str,
    chamar_modelo: ChamadorModelo,
    executor: Callable[[str, dict[str, Any]], dict[str, Any]],
    max_iteracoes: int,
    ao_resultado: Callable[[str, Any], None] | None = None,
    verificar_atalho: Callable[[], str | None] | None = None,
    modelo: str = MODELO_LLM,
    tool_choice_primeiro_turno: dict[str, Any] | None = None,
    ao_iniciar_usos: Callable[[list[str]], None] | None = None,
) -> tuple[str, list[str]]:
    """Loop genérico de tool-use da Converse API, usado pelo Orquestrador e por
    cada agente especialista.

    ``executor(nome_da_tool, entrada) -> dict`` executa a tool pedida pelo
    modelo — pode ser uma tool real (Gateway/KB), no caso de um especialista,
    ou o disparo de um agente especialista, no caso do Orquestrador. Quando o
    modelo pede mais de uma ferramenta no mesmo turno, elas são executadas em
    paralelo (``_executar_usos_em_paralelo``) para reduzir a latência total.

    ``ao_resultado(nome_da_tool, valor)``, se informado, é chamado na thread
    principal — nunca dentro das threads da execução paralela — uma vez por
    tool-use, na ordem em que o modelo as pediu. Use-o para bookkeeping que
    precise de ordem determinística (ex.: registrar agentes/ferramentas
    usados), já que efeitos colaterais dentro de ``executor`` ocorreriam em
    ordem de conclusão das threads, não de submissão.

    ``verificar_atalho()``, se informado, é chamado após cada turno de tools
    (depois de ``ao_resultado``). Se devolver um texto não vazio, o loop
    encerra IMEDIATAMENTE com esse texto, sem gastar mais uma chamada ao
    modelo — usado pelo Orquestrador para pular o round-trip final quando o
    Agente_Explicador já produziu a resposta definitiva (economiza uma
    chamada inteira ao LLM, o passo mais lento do pipeline).

    ``tool_choice_primeiro_turno``, se informado, força a Converse API a usar
    uma tool específica SÓ na primeira chamada ao modelo (ex.: garantir que
    Agente_Selecao_Ativos seja chamado quando o usuário pede dados da
    carteira — ver ``_mensagem_pede_dados_de_carteira``). A partir do segundo
    turno, a escolha volta a ser livre (``auto``), pois o modelo já viu o
    resultado da tool forçada e precisa decidir o que fazer com ele (só
    responder, ou pedir outra tool).

    ``ao_iniciar_usos(nomes)``, se informado, é chamado com os nomes das tools
    pedidas pelo modelo NESTE turno, ANTES de executá-las (ao contrário de
    ``ao_resultado``, que roda depois) — usado para reportar progresso (ex.:
    "Analisando sua carteira...") enquanto a chamada ainda está em andamento.

    Retorna ``(texto_final, nomes_de_tools_usadas)``.
    """
    usados: list[str] = []
    for indice_turno in range(max_iteracoes):
        tool_choice = tool_choice_primeiro_turno if indice_turno == 0 else None
        if tool_choice:
            resposta = chamar_modelo(
                mensagens, ferramentas, sistema, modelo, tool_choice=tool_choice
            )
        else:
            resposta = chamar_modelo(mensagens, ferramentas, sistema, modelo)
        saida = resposta.get("output", {}).get("message", {})
        mensagens.append(saida)
        if resposta.get("stopReason") != "tool_use":
            return _texto_da_mensagem(saida), usados

        usos = [
            bloco["toolUse"] for bloco in saida.get("content", []) if bloco.get("toolUse")
        ]
        usados.extend(uso["name"] for uso in usos)
        if ao_iniciar_usos is not None:
            ao_iniciar_usos([uso["name"] for uso in usos])
        pares = _executar_usos_em_paralelo(usos, executor)
        if ao_resultado is not None:
            for uso, (_, valor) in zip(usos, pares):
                ao_resultado(uso["name"], valor)
        mensagens.append(
            {"role": "user", "content": [msg for msg, _ in pares]}
        )
        if verificar_atalho is not None:
            atalho = verificar_atalho()
            if atalho:
                return atalho, usados

    texto = _texto_da_mensagem(mensagens[-1]) if mensagens else ""
    return (
        texto
        or "Não consegui concluir a análise no limite de etapas. Tente reformular.",
        usados,
    )


def _executar_agente_especialista(
    nome_agente: str,
    tarefa: str,
    user_id: str | None,
    chamar_tool: ChamadorTool,
    chamar_modelo: ChamadorModelo,
    recuperar_kb: RecuperadorKB | None,
) -> dict[str, Any]:
    """Executa um agente especialista (Risco, Selecao_Ativos ou Explicador) em
    seu próprio loop de tool-use, restrito à política de tools do agente
    (isolamento — Propriedade 6 do design). ``tarefa`` é a instrução recebida
    do Orquestrador (texto livre, campo ``pedido`` da meta-tool).

    Perfil e Macroeconômico não passam por aqui: são tools diretas do
    Orquestrador (ver ``executar_orquestrador``), não agentes.
    """
    spec = AGENTES_ESPECIALISTAS[nome_agente]
    mensagens: list[dict[str, Any]] = [{"role": "user", "content": [{"text": tarefa}]}]

    def executor(nome_tool: str, entrada: dict[str, Any]) -> dict[str, Any]:
        return _executar_ferramenta(nome_tool, entrada, user_id, chamar_tool, recuperar_kb)

    texto, usados = _loop_tool_use(
        mensagens,
        spec["tools"],
        spec["sistema"],
        chamar_modelo,
        executor,
        MAX_ITERACOES_ESPECIALISTA,
        modelo=spec.get("modelo", MODELO_LLM),
    )
    return {"agente": nome_agente, "resposta": texto, "ferramentas_usadas": usados}


_ETAPA_POR_TOOL: dict[str, str] = {
    "gerenciar_perfil_investidor": "Consultando seu perfil de investidor",
    "consultar_indicadores_economicos": "Consultando indicadores econômicos",
    "consultar_agente_risco": "Analisando o risco da carteira",
    "consultar_agente_selecao_ativos": "Analisando sua carteira e selecionando ativos",
    "consultar_agente_explicador": "Preparando a explicação da resposta",
}


def executar_orquestrador(
    entrada: dict[str, Any],
    chamar_tool: ChamadorTool,
    chamar_modelo: ChamadorModelo,
    recuperar_kb: RecuperadorKB | None = None,
    ao_progresso: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Conduz o Agente_Orquestrador: decide quais dos 5 agentes especialistas
    acionar, repassa a tarefa a cada um (que executa seu próprio loop de
    tool-use restrito às suas ferramentas) e devolve a resposta final.

    ``entrada`` deve conter ``user_id`` (do JWT) e ``mensagem`` (texto do
    usuário).

    ``ao_progresso(etapa)``, se informado, é chamado com uma descrição curta
    em português (ver ``_ETAPA_POR_TOOL``) sempre que o Orquestrador decide
    acionar uma tool direta ou um agente especialista — ANTES da execução, não
    depois, para o usuário ver o que está em andamento durante os ~1-2 min do
    pipeline (feedback de progresso do polling assíncrono; ver
    docs/decisoes-agentes-e-latencia.md). Chamado na thread principal, nunca
    dentro das threads de execução paralela.
    """
    user_id = entrada.get("user_id") or entrada.get("userId")
    mensagem = (
        entrada.get("mensagem")
        or entrada.get("prompt")
        or "Monte uma recomendação de investimento adequada ao meu perfil."
    )
    # Memória de curto prazo: semeia os turnos anteriores desta conversa antes da
    # mensagem atual, para o modelo manter o fio do diálogo (AgentCore Memory).
    mensagens: list[dict[str, Any]] = _mensagens_do_historico(
        entrada.get("historico") or []
    )
    mensagens.append({"role": "user", "content": [{"text": str(mensagem)}]})
    # Memória de longo prazo: preferências/fatos do investidor no system prompt.
    sistema = _sistema_com_memoria(
        SISTEMA_ORQUESTRADOR, entrada.get("memorias") or [], str(mensagem)
    )
    # Quando o pedido é sobre a carteira, força a tool no primeiro turno em vez
    # de confiar em instrução textual: testado isoladamente (regra "obrigatória"
    # no prompt, depois aviso isolado só para o turno) e o modelo respondeu da
    # memória de longo prazo sem chamar a tool nas duas vezes — só o
    # ``toolChoice`` da Converse API garante o determinismo (ver
    # docs/decisoes-agentes-e-latencia.md).
    tool_choice_primeiro_turno = (
        {"tool": {"name": "consultar_agente_selecao_ativos"}}
        if _mensagem_pede_dados_de_carteira(str(mensagem))
        else None
    )

    agentes_usados: list[str] = []
    ferramentas_usadas: list[str] = []
    # Guarda a resposta do Explicador para o atalho de encerramento (evita um
    # round-trip inteiro ao LLM só para o Orquestrador "repetir" esse texto).
    resposta_explicador: list[str] = []

    def executor(nome_tool: str, entrada_tool: dict[str, Any]) -> Any:
        # Só computação pura (chamadas de rede) — SEM mutar estado compartilhado
        # aqui, porque esta função roda dentro de threads quando o Orquestrador
        # aciona várias tools/agentes no mesmo turno (ver
        # _executar_usos_em_paralelo).
        if nome_tool in _TOOLS_DIRETAS_ORQUESTRADOR:
            # Tool direta do Gateway (Perfil/Macro): sem loop de LLM.
            return _executar_ferramenta(
                nome_tool, entrada_tool, user_id, chamar_tool, recuperar_kb
            )
        nome_agente = _META_PARA_AGENTE.get(nome_tool)
        if nome_agente is None:
            raise ValueError(f"Ferramenta/agente desconhecido: {nome_tool}")
        tarefa = str((entrada_tool or {}).get("pedido") or mensagem)
        return _executar_agente_especialista(
            nome_agente, tarefa, user_id, chamar_tool, chamar_modelo, recuperar_kb
        )

    def ao_resultado(nome_tool: str, resultado: Any) -> None:
        # Roda na thread principal, uma vez por tool-use, na ordem em que o
        # modelo pediu — bookkeeping determinístico mesmo com execução paralela.
        if nome_tool in _TOOLS_DIRETAS_ORQUESTRADOR:
            ferramentas_usadas.append(nome_tool)
            return
        if not isinstance(resultado, dict):
            return
        agente = resultado.get("agente") or _META_PARA_AGENTE.get(nome_tool, nome_tool)
        agentes_usados.append(agente)
        ferramentas_usadas.extend(resultado.get("ferramentas_usadas") or [])
        if agente == "Agente_Explicador":
            resposta_explicador.append(str(resultado.get("resposta") or ""))

    def verificar_atalho() -> str | None:
        # O Explicador consolida os achados e já produz a resposta final —
        # pular a chamada extra ao Orquestrador que só a "confirmaria".
        return resposta_explicador[-1] if resposta_explicador else None

    def ao_iniciar_usos(nomes_tools: list[str]) -> None:
        if ao_progresso is None or not nomes_tools:
            return
        etapas = [_ETAPA_POR_TOOL.get(nome, nome) for nome in nomes_tools]
        # Duas ou mais tools chamadas juntas no mesmo turno rodam em paralelo
        # (ver docstring do módulo) — junta as descrições em uma única etapa.
        ao_progresso(" + ".join(dict.fromkeys(etapas)))

    texto, _ = _loop_tool_use(
        mensagens,
        FERRAMENTAS_ORQUESTRADOR,
        sistema,
        chamar_modelo,
        executor,
        MAX_ITERACOES_ORQUESTRADOR,
        ao_resultado=ao_resultado,
        verificar_atalho=verificar_atalho,
        tool_choice_primeiro_turno=tool_choice_primeiro_turno,
        ao_iniciar_usos=ao_iniciar_usos,
    )
    return {
        "user_id": user_id,
        "resposta": texto,
        "agentes_usados": agentes_usados,
        "ferramentas_usadas": ferramentas_usadas,
        "disclaimer": DISCLAIMER_PROJECOES,
    }


# ---------------------------------------------------------------------------
# Agente_Perfil — avaliação de suitability por questionário (classificação
# determinística + justificativa gerada pelo LLM). Acionado diretamente no
# onboarding (não passa pelo Orquestrador — é uma ação síncrona própria).
# ---------------------------------------------------------------------------
PONTUACAO_MAXIMA = 18  # 6 perguntas × 3 pontos

_ROTULOS = {
    "objetivo": {
        1: "preservação do patrimônio",
        2: "equilíbrio entre segurança e crescimento",
        3: "maximizar o crescimento",
    },
    "horizonte": {
        1: "curto prazo (< 1 ano)",
        2: "médio prazo (1 a 3 anos)",
        3: "longo prazo (> 3 anos)",
    },
    "reacao_perda": {
        1: "baixa — resgataria os investimentos",
        2: "moderada — manteria e aguardaria",
        3: "alta — manteria ou investiria mais",
    },
    "experiencia": {
        1: "nenhuma ou apenas poupança",
        2: "renda fixa e/ou fundos",
        3: "renda variável (ações, FIIs, ETFs)",
    },
    "capacidade": {
        1: "baixa (até 10% da renda)",
        2: "moderada (10% a 30%)",
        3: "alta (mais de 30%)",
    },
    "conhecimento": {1: "básico", 2: "intermediário", 3: "avançado"},
}

# Dimensão de cada pergunta (P1..P6) para montar as características do perfil.
_DIMENSOES = (
    ("p1", "objetivo", "Objetivo financeiro"),
    ("p2", "horizonte", "Horizonte de investimento"),
    ("p3", "reacao_perda", "Tolerância a perdas"),
    ("p4", "experiencia", "Experiência"),
    ("p5", "capacidade", "Capacidade de investimento"),
    ("p6", "conhecimento", "Conhecimento"),
)


def _classificar(total: int, p3: int) -> tuple[str, str, bool]:
    """Faixa de pontuação → (perfil, tolerancia_risco, regra_consistencia).

    Faixas: 6–9 Conservador, 10–13 Moderado, 14–18 Arrojado. Regra de
    consistência: baixa tolerância a perdas (P3=1) impede a classificação
    Arrojado, rebaixando para Moderado, mesmo com pontuação alta.
    """
    if total <= 9:
        perfil, tolerancia = "conservador", "CONSERVADOR"
    elif total <= 13:
        perfil, tolerancia = "moderado", "MODERADO"
    else:
        perfil, tolerancia = "arrojado", "ARROJADO"

    regra_aplicada = False
    if perfil == "arrojado" and p3 == 1:
        perfil, tolerancia, regra_aplicada = "moderado", "MODERADO", True
    return perfil, tolerancia, regra_aplicada


def _justificativa_llm(
    chamar_modelo: ChamadorModelo,
    perfil: str,
    total: int,
    caracteristicas: list[dict[str, Any]],
    regra_aplicada: bool,
) -> str:
    """Gera a justificativa do perfil em linguagem natural (Opus, sem tools)."""
    linhas = "\n".join(
        f"- {c['dimensao']}: {c['valor']} ({c['pontos']} pt)" for c in caracteristicas
    )
    nota_regra = (
        "\nObservação: apesar da pontuação, o investidor indicou que resgataria "
        "tudo diante de uma queda — por isso NÃO foi classificado como arrojado "
        "(regra de consistência). Explique essa nuance."
        if regra_aplicada
        else ""
    )
    sistema = (
        "Você é o Agente de Perfil do GEVI. Escreva uma justificativa curta (2 a "
        "4 frases), clara e em português, explicando ao investidor por que ele foi "
        "classificado no perfil indicado, citando os principais fatores. Não use "
        "listas nem markdown; texto corrido."
    )
    prompt = (
        f"Perfil identificado: {perfil} (pontuação {total}/{PONTUACAO_MAXIMA}).\n"
        f"Características:\n{linhas}{nota_regra}"
    )
    try:
        resp = chamar_modelo(
            [{"role": "user", "content": [{"text": prompt}]}], [], sistema
        )
        texto = _texto_da_mensagem(resp.get("output", {}).get("message", {}))
        if texto:
            return texto
    except Exception:  # noqa: BLE001 - justificativa é complementar
        pass
    return (
        f"Você foi classificado como {perfil} com base nas suas respostas "
        f"(pontuação {total}/{PONTUACAO_MAXIMA}), considerando objetivo, horizonte, "
        "tolerância a perdas, experiência, capacidade e conhecimento."
    )


def avaliar_perfil(
    entrada: dict[str, Any],
    chamar_tool: ChamadorTool,
    chamar_modelo: ChamadorModelo,
) -> dict[str, Any]:
    """Avalia o questionário de suitability e persiste o perfil (Agente_Perfil).

    ``entrada`` traz ``respostas`` (p1..p6, valores 1–3), ``user_id`` (do JWT) e,
    para persistir, ``email`` e ``nome``. Classificação determinística; o LLM
    apenas redige a justificativa. Faz upsert via ``gerenciar_perfil_investidor``.
    """
    respostas = entrada.get("respostas") or {}
    user_id = entrada.get("user_id") or entrada.get("userId")

    def _pt(chave: str) -> int:
        try:
            valor = int(respostas.get(chave, 0))
        except (TypeError, ValueError):
            valor = 0
        return valor if valor in (1, 2, 3) else 1

    pontos = {chave: _pt(chave) for chave, _, _ in _DIMENSOES}
    total = sum(pontos.values())
    perfil, tolerancia_risco, regra = _classificar(total, pontos["p3"])
    nivel_conhecimento = "avancado" if pontos["p6"] == 3 else "basico"

    caracteristicas = [
        {
            "dimensao": titulo,
            "valor": _ROTULOS[dim][pontos[chave]],
            "pontos": pontos[chave],
        }
        for chave, dim, titulo in _DIMENSOES
    ]
    principais_fatores = [
        c["dimensao"]
        for c in sorted(caracteristicas, key=lambda c: c["pontos"], reverse=True)[:3]
    ]
    justificativa = _justificativa_llm(
        chamar_modelo, perfil, total, caracteristicas, regra
    )

    # Upsert do perfil (cria se não existir; atualiza caso já exista).
    persistido = False
    try:
        # Os dados do perfil vão DENTRO de ``perfil`` — é o campo declarado no
        # schema da tool no Gateway (que descarta campos fora do schema).
        perfil_dados = {
            "user_id": user_id,
            "email": entrada.get("email"),
            "nome": entrada.get("nome"),
            "nivel_conhecimento": nivel_conhecimento,
            "tolerancia_risco": tolerancia_risco,
        }
        atual = chamar_tool(
            "gerenciar_perfil_investidor", {"operacao": "ler", "user_id": user_id}
        )
        existe = isinstance(atual, dict) and atual.get("sucesso")
        resposta = chamar_tool(
            "gerenciar_perfil_investidor",
            {
                "operacao": "atualizar" if existe else "criar",
                "user_id": user_id,
                "perfil": perfil_dados,
            },
        )
        persistido = bool(isinstance(resposta, dict) and resposta.get("sucesso"))
    except Exception:  # noqa: BLE001 - persistência não deve derrubar a resposta
        persistido = False

    return {
        "user_id": user_id,
        "perfil": perfil,
        "pontuacao": total,
        "pontuacao_maxima": PONTUACAO_MAXIMA,
        "tolerancia_risco": tolerancia_risco,
        "nivel_conhecimento": nivel_conhecimento,
        "caracteristicas": caracteristicas,
        "principais_fatores": principais_fatores,
        "regra_consistencia_aplicada": regra,
        "justificativa": justificativa,
        "persistido": persistido,
    }
