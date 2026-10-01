"use client";

// Chat com o Assistente IA — portado do mockup
// sistema/front/chat_com_assistente_ia_gevi/code.html, em AppShell fullBleed
// (duas colunas: lista de conversas + área de chat com input fixo). O envio
// aciona o pipeline dos 5 agentes (POST /chat no proxy -> Runtime) e a resposta
// exibe a explicação consolidada (XAI) + conteúdo educativo + disclaimer.
import { useEffect, useRef, useState } from "react";
import AuthGuard from "@/components/AuthGuard";
import AppShell from "@/components/AppShell";
import Markdown from "@/components/Markdown";
import {
  enviarChat,
  listarConversas,
  obterConversa,
  excluirConversa,
  type ConversaResumo,
} from "@/lib/api";

// Nome e foto da assistente. O avatar é servido do próprio bucket do
// frontend (public/clara-avatar.png) em vez de uma URL externa — mais
// confiável (não depende de um link de terceiro que pode expirar).
const CLARA_NOME = "ClaraInvest";
const CLARA_AVATAR = "/clara-avatar.png";

type Mensagem = {
  autor: "user" | "ia";
  texto: string;
  educativo?: string[] | null;
  disclaimer?: string;
  hora: string;
};

function agora(): string {
  return new Date().toLocaleTimeString("pt-BR", {
    hour: "2-digit",
    minute: "2-digit",
  });
}

// Data relativa (mesmo padrão do "última alteração" no Dashboard): "hoje",
// "ontem", "há N dias" para conversas recentes; data completa para antigas.
// Sem ano fica ambíguo quando a lista acumula meses/anos.
function formatarData(iso: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const dias = Math.floor((Date.now() - d.getTime()) / 86_400_000);
  if (dias <= 0) return "hoje";
  if (dias === 1) return "ontem";
  if (dias < 7) return `há ${dias} dias`;
  return d.toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" });
}

// Chips de sugestão exibidos na área vazia do chat (só uma mensagem de
// boas-vindas visível). Aproveita o espaço em branco para dar ao usuário
// "atalhos" comuns, sem obrigá-lo a saber o que perguntar.
const SUGESTOES = [
  "Analisar minha carteira",
  "Como está o cenário econômico?",
  "Sugerir um rebalanceamento",
  "Explicar o que é Selic",
];

function bemVindo(): Mensagem {
  return {
    autor: "ia",
    texto:
      "Olá! Sou a ClaraInvest, assistente da GEVI. Posso analisar seu perfil, o cenário econômico atual e sugerir uma carteira. Me conte o que você quer investir ou faça uma pergunta.",
    hora: agora(),
  };
}

export default function ChatPage() {
  const [mensagens, setMensagens] = useState<Mensagem[]>(() => [bemVindo()]);
  const [texto, setTexto] = useState("");
  const [carregando, setCarregando] = useState(false);
  // Etapa atual do pipeline durante o carregamento (ex.: "Analisando sua
  // carteira..."), atualizada pelo polling em enviarChat — feedback de
  // progresso incremental sem streaming real de tokens.
  const [etapaAtual, setEtapaAtual] = useState("");
  const [conversas, setConversas] = useState<ConversaResumo[]>([]);
  const [conversaId, setConversaId] = useState<string | null>(null);
  const fimRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fimRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [mensagens, carregando]);

  useEffect(() => {
    listarConversas()
      .then(setConversas)
      .catch(() => {});
  }, []);

  async function atualizarLista() {
    try {
      setConversas(await listarConversas());
    } catch {
      /* ignora */
    }
  }

  async function abrirConversa(cid: string) {
    if (carregando) return;
    try {
      const c = await obterConversa(cid);
      setMensagens(
        c.mensagens.map((m) => ({ autor: m.autor, texto: m.texto, hora: m.hora })),
      );
      setConversaId(cid);
    } catch {
      /* ignora */
    }
  }

  async function enviarMensagem(conteudo: string) {
    if (!conteudo || carregando) return;
    setMensagens((m) => [...m, { autor: "user", texto: conteudo, hora: agora() }]);
    setTexto("");
    setCarregando(true);
    setEtapaAtual("Analisando seu perfil e o cenário econômico…");
    try {
      const { resultado: r, conversaId: cid } = await enviarChat(
        { mensagem: conteudo },
        conversaId ?? undefined,
        setEtapaAtual,
      );
      if (cid) setConversaId(cid);
      const respostaTexto = r.resposta || r.explicacao?.explicacao;
      setMensagens((m) => [
        ...m,
        {
          autor: "ia",
          texto:
            respostaTexto ||
            "Processei sua solicitação, mas não consegui gerar uma resposta. Tente reformular.",
          educativo: r.explicacao?.conteudo_educativo ?? null,
          disclaimer: r.disclaimer,
          hora: agora(),
        },
      ]);
      atualizarLista();
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Falha ao consultar o assistente.";
      setMensagens((m) => [
        ...m,
        {
          autor: "ia",
          texto: `Não foi possível concluir a análise agora (${msg}). A consulta pode ter excedido o tempo limite — tente novamente.`,
          hora: agora(),
        },
      ]);
    } finally {
      setCarregando(false);
      setEtapaAtual("");
    }
  }

  function enviar() {
    void enviarMensagem(texto.trim());
  }

  function aoTeclar(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      enviar();
    }
  }

  function novaConversa() {
    if (carregando) return;
    setConversaId(null);
    setMensagens([bemVindo()]);
    setTexto("");
  }

  // Exclui uma conversa (otimista) e, se for a aberta, volta para nova conversa.
  async function excluir(e: React.MouseEvent, cid: string) {
    e.stopPropagation();
    if (carregando) return;
    const anterior = conversas;
    setConversas((cs) => cs.filter((c) => c.conversaId !== cid));
    if (cid === conversaId) novaConversa();
    try {
      await excluirConversa(cid);
    } catch {
      setConversas(anterior); // reverte se a exclusão falhar
    }
  }

  return (
    <AuthGuard>
      <AppShell fullBleed>
        <div className="flex h-full overflow-hidden">
          {/* Lista de conversas */}
          <aside className="hidden md:flex w-80 border-r border-outline-variant bg-surface-container-lowest flex-col h-full shrink-0">
            <div className="p-md border-b border-outline-variant">
              <h2 className="font-headline-md text-headline-md text-on-surface mb-sm">
                Conversas Recentes
              </h2>
              {/* Botão em destaque (fundo colorido) — antes tinha borda sutil
                  igual aos itens da lista, ficando quase invisível como "ação
                  principal" daquela coluna. */}
              <button
                onClick={novaConversa}
                disabled={carregando}
                className="w-full flex items-center justify-center gap-xs py-sm px-sm bg-primary-container text-on-primary rounded-lg hover:bg-on-primary-fixed-variant transition-colors font-headline-md text-sm shadow-sm disabled:opacity-50"
              >
                <span className="material-symbols-outlined text-base">add</span>
                Nova Conversa
              </button>
            </div>
            <div className="flex-grow overflow-y-auto chat-scroll p-sm space-y-xs">
              {conversas.length === 0 && (
                <p className="font-body-sm text-body-sm text-on-surface-variant text-center p-sm">
                  Nenhuma conversa ainda. Envie uma mensagem para começar.
                </p>
              )}
              {conversas.map((c) => {
                const ativa = c.conversaId === conversaId;
                return (
                  <div
                    key={c.conversaId}
                    className={
                      ativa
                        ? "group flex items-start gap-sm w-full p-sm rounded-lg bg-secondary-container/20 border-l-4 border-secondary-container shadow-sm"
                        : "group flex items-start gap-sm w-full p-sm rounded-lg hover:bg-surface-container border-l-4 border-transparent transition-colors"
                    }
                  >
                    {/* Ícone à esquerda dá peso visual ao item da lista, em
                        vez de deixá-lo como uma linha solta de texto. */}
                    <span
                      className={`material-symbols-outlined text-lg mt-0.5 shrink-0 ${
                        ativa ? "text-secondary-container" : "text-on-surface-variant"
                      }`}
                    >
                      chat_bubble
                    </span>
                    <button
                      onClick={() => abrirConversa(c.conversaId)}
                      className="flex-1 min-w-0 text-left"
                    >
                      <div className="flex flex-col gap-0.5">
                        <span
                          className={`font-body-sm text-body-sm font-semibold truncate ${
                            ativa ? "text-on-surface" : "text-on-surface"
                          }`}
                        >
                          {c.titulo}
                        </span>
                        <span className="font-label-caps text-label-caps text-on-surface-variant">
                          {formatarData(c.atualizadaEm)}
                        </span>
                      </div>
                    </button>
                    {/* Excluir só aparece no hover do item (group-hover) — antes
                        ficava sempre visível, poluindo a lista e aumentando o
                        risco de clique acidental. */}
                    <button
                      onClick={(e) => excluir(e, c.conversaId)}
                      disabled={carregando}
                      aria-label="Excluir conversa"
                      title="Excluir conversa"
                      className="shrink-0 p-1 rounded-md flex items-center justify-center opacity-0 group-hover:opacity-100 focus:opacity-100 text-on-surface-variant/70 hover:text-error hover:bg-surface-variant transition-opacity disabled:opacity-40"
                    >
                      <span className="material-symbols-outlined text-[18px]">
                        delete
                      </span>
                    </button>
                  </div>
                );
              })}
            </div>
          </aside>

          {/* Área principal do chat */}
          <section className="flex-grow flex flex-col bg-background h-full min-w-0">
            {/* Cabeçalho do chat */}
            <div className="h-16 px-md flex items-center border-b border-outline-variant bg-surface-container-lowest shrink-0">
              <div className="relative w-10 h-10 rounded-full overflow-hidden mr-sm shrink-0 border border-outline-variant">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  alt={CLARA_NOME}
                  src={CLARA_AVATAR}
                  className="w-full h-full object-cover"
                  style={{ objectPosition: "50% 12%" }}
                />
                <div className="absolute bottom-0 right-0 w-3 h-3 bg-secondary-container rounded-full border-2 border-surface-container-lowest" />
              </div>
              <div>
                <h1 className="font-headline-md text-headline-md text-on-surface leading-tight">
                  {CLARA_NOME}
                </h1>
                <p className="font-label-caps text-label-caps text-secondary-container">
                  Online • Especialista em Análise de Mercado
                </p>
              </div>
            </div>

            {/* Mensagens */}
            <div className="flex-grow overflow-y-auto chat-scroll p-lg flex flex-col gap-lg">
              <div className="flex justify-center">
                <span className="bg-surface-container px-sm py-xs rounded-full font-label-caps text-label-caps text-on-surface-variant">
                  Hoje
                </span>
              </div>

              {mensagens.map((msg, i) =>
                msg.autor === "user" ? (
                  <div key={i} className="flex justify-end">
                    <div className="max-w-[70%] bg-primary-container text-on-primary-container p-md rounded-2xl rounded-tr-none shadow-sm">
                      <p className="font-body-lg text-body-lg whitespace-pre-wrap">
                        {msg.texto}
                      </p>
                      <div className="mt-xs text-right opacity-70 font-label-caps text-[10px]">
                        {msg.hora}
                      </div>
                    </div>
                  </div>
                ) : (
                  <div key={i} className="flex justify-start">
                    <div className="flex flex-col gap-xs max-w-[80%]">
                      <div className="flex items-center gap-xs ml-sm mb-1">
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img
                          alt={CLARA_NOME}
                          src={CLARA_AVATAR}
                          className="w-5 h-5 rounded-full object-cover border border-outline-variant"
                          style={{ objectPosition: "50% 12%" }}
                        />
                        <span className="font-headline-md text-sm text-on-surface">
                          {CLARA_NOME}
                        </span>
                      </div>
                      <div className="bg-surface-container-lowest border border-outline-variant text-on-surface p-md rounded-2xl rounded-tl-none shadow-sm">
                        <Markdown>{msg.texto}</Markdown>
                        {msg.educativo && msg.educativo.length > 0 && (
                          <ul className="list-disc pl-md space-y-xs font-body-lg text-body-lg mt-md text-on-surface-variant">
                            {msg.educativo.map((item, j) => (
                              <li key={j}>{item}</li>
                            ))}
                          </ul>
                        )}
                        {msg.disclaimer && (
                          <p className="mt-md font-label-caps text-[10px] text-on-surface-variant opacity-70">
                            {msg.disclaimer}
                          </p>
                        )}
                        <div className="mt-sm text-left opacity-70 font-label-caps text-[10px]">
                          {msg.hora}
                        </div>
                      </div>
                    </div>
                  </div>
                ),
              )}

              {carregando && (
                <div className="flex justify-start">
                  <div className="bg-surface-container-lowest border border-outline-variant text-on-surface-variant p-md rounded-2xl rounded-tl-none shadow-sm font-body-sm flex items-center gap-2">
                    <span className="material-symbols-outlined text-base animate-spin">
                      progress_activity
                    </span>
                    {etapaAtual || "Analisando seu perfil e o cenário econômico…"}
                  </div>
                </div>
              )}

              {/* Chips de sugestão: só quando é uma conversa "vazia" (só a
                  mensagem de boas-vindas e sem conversaId aberto). Aproveita
                  o espaço em branco para dar ao usuário atalhos comuns e
                  reduzir o "medo da página em branco". Tons de azul (secondary
                  do tema) para parecerem "convite" e diferenciar do input
                  neutro logo abaixo. */}
              {!carregando && !conversaId && mensagens.length === 1 && (
                <div className="flex flex-col gap-sm items-start ml-sm max-w-xl">
                  <p className="font-label-caps text-label-caps text-primary-container uppercase">
                    Sugestões para começar
                  </p>
                  {/* Grid 2 colunas: todas as sugestões ficam do mesmo
                      tamanho, independente do comprimento do texto (antes
                      cada chip era largo apenas o suficiente para o texto,
                      criando visuais desalinhados). */}
                  <div className="grid grid-cols-2 gap-2 w-full">
                    {SUGESTOES.map((s) => (
                      <button
                        key={s}
                        onClick={() => void enviarMensagem(s)}
                        className="px-md py-xs bg-secondary-fixed border border-secondary-fixed-dim rounded-full text-body-sm text-on-secondary-fixed-variant font-medium hover:bg-secondary-fixed-dim hover:border-secondary-container transition-colors shadow-sm text-center"
                      >
                        {s}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              <div ref={fimRef} />
            </div>

            {/* Área de input */}
            <div className="p-md bg-surface-container-lowest border-t border-outline-variant shrink-0">
              <div className="flex items-end gap-sm bg-surface-container rounded-xl p-xs border border-outline-variant focus-within:ring-2 focus-within:ring-secondary focus-within:border-transparent transition-all">
                <textarea
                  className="w-full bg-transparent border-none focus:ring-0 resize-none max-h-32 py-sm font-body-lg text-on-surface placeholder:text-on-surface-variant/50"
                  placeholder="Digite sua mensagem..."
                  rows={1}
                  style={{ minHeight: "44px" }}
                  value={texto}
                  onChange={(e) => setTexto(e.target.value)}
                  onKeyDown={aoTeclar}
                  disabled={carregando}
                />
                <button
                  className="p-sm bg-primary-container text-on-primary-container rounded-lg hover:bg-opacity-90 transition-colors shrink-0 flex items-center justify-center disabled:opacity-50"
                  onClick={enviar}
                  disabled={carregando || !texto.trim()}
                  aria-label="Enviar"
                >
                  <span
                    className="material-symbols-outlined"
                    style={{ fontVariationSettings: "'FILL' 1" }}
                  >
                    send
                  </span>
                </button>
              </div>
              <p className="mt-xs text-center text-[11px] text-on-surface-variant">
                A IA da GEVI pode gerar informações imprecisas. Verifique
                métricas importantes.
              </p>
            </div>
          </section>
        </div>
      </AppShell>
    </AuthGuard>
  );
}
