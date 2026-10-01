// Cliente do HTTP API (proxy) que fala com o AgentCore.
// O proxy é autorizado pelo Cognito: enviamos o *access token* no header
// Authorization. As rotas são:
//   POST /chat            -> pipeline dos 5 agentes (Runtime)
//   POST /gateway/{tool}  -> tools do Gateway (dados reais)
// A URL base é o contrato exposto pela ApiStack (CfnOutput SaidaApiUrl); é
// pública por design e pode ser default versionado, com override por env.
import { fetchAuthSession, fetchUserAttributes } from "aws-amplify/auth";

const API_BASE =
  process.env.NEXT_PUBLIC_API_URL ??
  "API_URL";

/** Access token do Cognito (claim client_id — o que o authorizer valida). */
async function tokenAcesso(): Promise<string> {
  const sessao = await fetchAuthSession();
  const token = sessao.tokens?.accessToken?.toString();
  if (!token) throw new Error("Sessão expirada. Faça login novamente.");
  return token;
}

async function postar<T>(caminho: string, corpo: unknown): Promise<T> {
  const token = await tokenAcesso();
  const resp = await fetch(`${API_BASE}${caminho}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(corpo ?? {}),
  });
  const texto = await resp.text();
  let dados: unknown = null;
  try {
    dados = texto ? JSON.parse(texto) : null;
  } catch {
    dados = texto;
  }
  if (!resp.ok) {
    const msg =
      (dados as { erro?: unknown })?.erro != null
        ? JSON.stringify((dados as { erro: unknown }).erro)
        : `Falha ${resp.status}`;
    throw new Error(typeof msg === "string" ? msg : "Falha na requisição.");
  }
  return dados as T;
}

/** Chama uma tool do Gateway e devolve o campo ``resultado``. */
export async function chamarTool<T = unknown>(
  tool: string,
  argumentos: Record<string, unknown> = {},
): Promise<T> {
  const resp = await postar<{ resultado: T }>(`/gateway/${tool}`, {
    arguments: argumentos,
  });
  return resp.resultado;
}

export type RespostaChat = {
  // Formato novo (orquestrador LLM): resposta em linguagem natural.
  resposta?: string;
  ferramentas_usadas?: string[];
  disclaimer?: string;
  // Compat: formato antigo (pipeline sequencial).
  explicacao?: {
    nivel_linguagem?: string;
    explicacao?: string;
    conteudo_educativo?: string[] | null;
  };
  recomendacao?: unknown;
  analise_risco?: unknown;
};

/**
 * Invoca o orquestrador (chat) de forma assíncrona: inicia um job e faz polling
 * até concluir. O chat completo (Opus + várias tools) excede o teto de 30s do
 * API Gateway, então o backend processa em segundo plano e a UI consulta o
 * status até ter a resposta.
 */
export async function enviarChat(
  payload: {
    mensagem?: string;
    valor_inicial?: number;
    prazo_meses?: number;
    tipo_ativo?: string;
  },
  conversaId?: string,
  onProgresso?: (etapa: string) => void,
): Promise<{ resultado: RespostaChat; conversaId: string }> {
  const inicio = await postar<{ jobId?: string; conversaId?: string }>("/chat", {
    acao: "chat",
    ...payload,
    conversaId,
  });
  const jobId = inicio.jobId;
  const cid = inicio.conversaId ?? conversaId ?? "";
  if (!jobId) throw new Error("Não foi possível iniciar a análise.");

  const LIMITE_MS = 180_000; // 3 min
  const INTERVALO_MS = 2_500;
  const t0 = Date.now();
  let ultimaEtapa = "";
  while (Date.now() - t0 < LIMITE_MS) {
    await new Promise((r) => setTimeout(r, INTERVALO_MS));
    let st: { status?: string; resultado?: RespostaChat; erro?: unknown; etapa?: string };
    try {
      st = await postar("/chat", { acao: "status", jobId });
    } catch {
      continue; // erro transitório na consulta: segue tentando até o limite
    }
    // Etapa atual do pipeline (ex.: "Analisando sua carteira..."), gravada
    // pelo Runtime durante o processamento — feedback incremental sem
    // streaming real (a resposta só sai completa ao final; ver
    // docs/decisoes-agentes-e-latencia.md).
    if (st.etapa && st.etapa !== ultimaEtapa) {
      ultimaEtapa = st.etapa;
      onProgresso?.(st.etapa);
    }
    if (st.status === "done" && st.resultado)
      return { resultado: st.resultado, conversaId: cid };
    if (st.status === "error") {
      throw new Error(
        typeof st.erro === "string" ? st.erro : "Falha ao gerar a resposta.",
      );
    }
    // pending / unknown → continua o polling
  }
  throw new Error("A análise demorou mais que o esperado. Tente novamente.");
}

export type ConversaResumo = {
  conversaId: string;
  titulo: string;
  atualizadaEm: string;
};

export type MensagemPersistida = { autor: "user" | "ia"; texto: string; hora: string };

/** Lista as conversas salvas do usuário (mais recentes primeiro). */
export async function listarConversas(): Promise<ConversaResumo[]> {
  const resp = await postar<{ conversas?: ConversaResumo[] }>("/chat", {
    acao: "listar_conversas",
  });
  return resp.conversas ?? [];
}

/** Carrega as mensagens de uma conversa salva. */
export async function obterConversa(
  conversaId: string,
): Promise<{ titulo: string; mensagens: MensagemPersistida[] }> {
  const resp = await postar<{ titulo?: string; mensagens?: MensagemPersistida[] }>(
    "/chat",
    { acao: "obter_conversa", conversaId },
  );
  return { titulo: resp.titulo ?? "", mensagens: resp.mensagens ?? [] };
}

/** Exclui uma conversa salva do usuário. */
export async function excluirConversa(conversaId: string): Promise<void> {
  await postar("/chat", { acao: "excluir_conversa", conversaId });
}

// Indicador econômico como retornado por consultar_indicadores_economicos.
export type Indicador = {
  indicator_id: string;
  valor?: number;
  valor_anterior?: number;
  valor_anualizado?: number | null; // taxa em base anual (% a.a.), quando aplicável
  unidade?: string; // ex.: a.a., a.d., a.m., preço
  variacao_percentual?: number;
  disponivel?: boolean;
  desatualizado?: boolean;
  date?: string;
};

export type Notificacao = {
  id: string;
  titulo: string;
  mensagem: string;
  timestamp: string;
  lida: boolean;
};

export type Limiar = { indicator_id: string; limiar_percentual: number };

/** Lista notificações do usuário (alertas de mercado + proativos da carteira). */
export async function listarNotificacoes(): Promise<{
  notificacoes: Notificacao[];
  nao_lidas: number;
}> {
  const resp = await postar<{ notificacoes?: Notificacao[]; nao_lidas?: number }>(
    "/chat",
    { acao: "listar_notificacoes" },
  );
  return { notificacoes: resp.notificacoes ?? [], nao_lidas: resp.nao_lidas ?? 0 };
}

/** Marca as notificações de mercado armazenadas como lidas. */
export async function marcarNotificacoesLidas(): Promise<void> {
  await postar("/chat", { acao: "marcar_lidas" });
}

/**
 * Limpa todas as notificações visíveis do usuário (mercado + proativas).
 * Diferente de ``marcarNotificacoesLidas``, que só zera o badge: aqui os
 * alertas somem de fato e ficam descartados até o ID mudar (ex.: outro
 * ativo cair em concentração excessiva). Ver ``_limpar_notificacoes`` no
 * proxy.
 */
export async function limparNotificacoes(): Promise<void> {
  await postar("/chat", { acao: "limpar_notificacoes" });
}

/** Lê os limiares de alerta configurados pelo usuário. */
export async function listarLimiares(): Promise<Limiar[]> {
  const resp = await postar<{ limiares?: Limiar[] }>("/chat", {
    acao: "listar_limiares",
  });
  return resp.limiares ?? [];
}

/** Substitui os limiares de alerta do usuário. */
export async function salvarLimiares(limiares: Limiar[]): Promise<Limiar[]> {
  const resp = await postar<{ limiares?: Limiar[] }>("/chat", {
    acao: "salvar_limiares",
    limiares,
  });
  return resp.limiares ?? [];
}

export type PontoSerie = { rotulo: string; valor: number };

/**
 * Série histórica mensal dos indicadores (BCB/SGS) para o gráfico de evolução.
 * Retorna um mapa indicador -> pontos mensais (mais antigo → mais recente).
 */
export async function serieHistorica(): Promise<Record<string, PontoSerie[]>> {
  const resp = await postar<{ series?: Record<string, PontoSerie[]> }>("/chat", {
    acao: "serie_historica",
  });
  return resp.series ?? {};
}

/** Consulta os indicadores macroeconômicos (Selic, IPCA, Dólar, CDI). */
export async function consultarIndicadores(
  indicadores?: string[],
): Promise<Indicador[]> {
  const args = indicadores ? { indicadores } : {};
  const resp = await chamarTool<{ indicadores?: Indicador[] }>(
    "consultar_indicadores_economicos",
    args,
  );
  return resp?.indicadores ?? [];
}

/** Analisa o portfólio do usuário autenticado (user_id vem do JWT no proxy). */
export async function analisarPortfolio<T = any>(
  tolerancia_risco?: string,
): Promise<T> {
  const args = tolerancia_risco ? { tolerancia_risco } : {};
  return chamarTool<T>("analisar_portfolio", args);
}

// Ativo da carteira cadastrado manualmente pelo investidor.
export type AtivoCarteira = {
  assetId: string;
  tipo_ativo: string; // RENDA_FIXA | RENDA_VARIAVEL | FII | CRIPTO
  valor: number;
  percentual?: number;
};

/** Lista os ativos da carteira do usuário (cadastro manual, dev-Portfolios). */
export async function listarPortfolio(): Promise<{
  ativos: AtivoCarteira[];
  valor_total: number;
  atualizadaEm: string;
  ultimaAlteracaoDescricao: string;
}> {
  const resp = await postar<{
    ativos?: AtivoCarteira[];
    valor_total?: number;
    atualizadaEm?: string;
    ultimaAlteracaoDescricao?: string;
  }>("/chat", { acao: "listar_portfolio" });
  return {
    ativos: resp.ativos ?? [],
    valor_total: resp.valor_total ?? 0,
    atualizadaEm: resp.atualizadaEm ?? "",
    ultimaAlteracaoDescricao: resp.ultimaAlteracaoDescricao ?? "",
  };
}

/**
 * Substitui a carteira do usuário pelos ativos informados (replace-all).
 * O backend valida o tipo e calcula o percentual de cada ativo pelo valor.
 */
export async function salvarPortfolio(
  ativos: { assetId: string; tipo_ativo: string; valor: number }[],
): Promise<{ ativos: AtivoCarteira[]; valor_total: number }> {
  const resp = await postar<{ ativos?: AtivoCarteira[]; valor_total?: number }>(
    "/chat",
    { acao: "salvar_portfolio", ativos },
  );
  return { ativos: resp.ativos ?? [], valor_total: resp.valor_total ?? 0 };
}


type RespostaPerfil = { sucesso?: boolean; codigo?: string };

/**
 * Garante que o perfil do investidor exista no DynamoDB (idempotente).
 *
 * Chamada após o login: primeiro lê o perfil (o proxy injeta o user_id do JWT);
 * se não existir, cria a partir dos atributos do Cognito coletados no cadastro
 * (nome, e-mail, custom:perfil_risco, custom:nivel_conhec). Os campos vão no
 * TOPO dos argumentos (sem envelope "perfil") para que o user_id injetado pelo
 * proxy seja incorporado ao perfil pelo handler fn-perfil-usuario. É best-effort:
 * qualquer falha é engolida para não bloquear o fluxo de login.
 */
export async function sincronizarPerfilInvestidor(): Promise<void> {
  try {
    const atual = await chamarTool<RespostaPerfil>("gerenciar_perfil_investidor", {
      operacao: "ler",
    });
    if (atual?.sucesso) return; // perfil já existe
  } catch {
    // segue para tentar criar
  }

  try {
    const attrs = await fetchUserAttributes();
    const email = attrs.email;
    const nome = attrs.name;
    const tolerancia_risco = attrs["custom:perfil_risco"];
    const nivel_conhecimento = attrs["custom:nivel_conhec"];
    // Sem os campos obrigatórios (usuários antigos, pré-cadastro com perfil), não
    // dá para criar um perfil válido — encerra silenciosamente.
    if (!email || !nome || !tolerancia_risco || !nivel_conhecimento) return;

    await chamarTool("gerenciar_perfil_investidor", {
      operacao: "criar",
      email,
      nome,
      tolerancia_risco,
      nivel_conhecimento,
    });
  } catch {
    // best-effort: não bloqueia o login se a criação falhar.
  }
}


export type Caracteristica = { dimensao: string; valor: string; pontos: number };

export type ResultadoPerfil = {
  perfil: string;
  pontuacao: number;
  pontuacao_maxima: number;
  tolerancia_risco: string;
  nivel_conhecimento: string;
  caracteristicas: Caracteristica[];
  principais_fatores: string[];
  regra_consistencia_aplicada: boolean;
  justificativa: string;
  persistido: boolean;
};

export type PerfilInvestidor = {
  nome?: string;
  email?: string;
  tolerancia_risco?: string; // CONSERVADOR | MODERADO | ARROJADO
  nivel_conhecimento?: string; // basico | avancado
};

/** Lê o perfil do investidor autenticado; retorna null se não houver. */
export async function lerPerfil(): Promise<PerfilInvestidor | null> {
  try {
    const r = await chamarTool<{ sucesso?: boolean; perfil?: PerfilInvestidor }>(
      "gerenciar_perfil_investidor",
      { operacao: "ler" },
    );
    return r?.sucesso && r.perfil ? r.perfil : null;
  } catch {
    return null;
  }
}

/** Verifica se o investidor já possui perfil cadastrado (via tool de perfil). */
export async function perfilExiste(): Promise<boolean> {
  try {
    const r = await chamarTool<{ sucesso?: boolean }>(
      "gerenciar_perfil_investidor",
      { operacao: "ler" },
    );
    return !!r?.sucesso;
  } catch {
    return false;
  }
}

/**
 * Envia as respostas do questionário de suitability para o Agente_Perfil
 * avaliar (classificação determinística + justificativa por IA) e persistir.
 * ``respostas`` = { p1..p6: 1|2|3 }.
 */
export async function avaliarPerfil(
  respostas: Record<string, number>,
): Promise<ResultadoPerfil> {
  let email: string | undefined;
  let nome: string | undefined;
  try {
    const attrs = await fetchUserAttributes();
    email = attrs.email;
    nome = attrs.name;
  } catch {
    /* segue sem email/nome (upsert pode falhar, mas a avaliação retorna) */
  }
  const resp = await postar<{ resultado: ResultadoPerfil }>("/chat", {
    acao: "avaliar_perfil",
    respostas,
    email,
    nome,
  });
  return resp.resultado;
}
