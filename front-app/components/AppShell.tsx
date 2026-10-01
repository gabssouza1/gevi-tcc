"use client";

// Layout das telas logadas — TopNav + SideNav compartilhados, portados
// fielmente do mockup do dashboard (sistema/front/dashboard_.../code.html).
// A interatividade dos dropdowns (notificações/perfil) foi convertida de JS
// vanilla para estado React; "Sair" faz signOut no Cognito.
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { signOut, fetchUserAttributes } from "aws-amplify/auth";
import Disclaimer from "@/components/Disclaimer";
import {
  limparNotificacoes,
  listarNotificacoes,
  marcarNotificacoesLidas,
  type Notificacao,
} from "@/lib/api";

const LOGO_SRC =
  "https://lh3.googleusercontent.com/aida-public/AB6AXuB96QLXTegMNeCe4FRoLQbZqVQ4FWjeLvCTywvh4WKpzYMoKws_EOUdwkA7_VLOgNaf0ARccWcosTTc8IKZ5K_XW5KdwzxBAM1xod_9ZojNzje7VNSqr9zhl-tIebMIxBARcaLqLxb2KLVwhWEqhX6YuusDBwGTMRvZegM8UjroPYpQ31KAIbA53NrLMku6vNDFpaxOkk3MeKYKXjjjmHBo8ngIicohCsrO4upKoGamokG9Gs8DrWWVor1vDMi7wNCTnoo";

const NAV = [
  { href: "/dashboard", icon: "grid_view", label: "Início" },
  { href: "/investimentos", icon: "account_balance_wallet", label: "Investimentos" },
  { href: "/chat", icon: "chat", label: "Chat" },
  { href: "/configuracoes", icon: "settings", label: "Configurações" },
  { href: "/suporte", icon: "help", label: "Suporte" },
];

// Primeiro nome do usuário para exibição (fallback amigável).
function primeiroNome(attrs: Record<string, string | undefined>): string {
  const nome = (attrs.name || "").trim();
  if (nome) return nome.split(/\s+/)[0];
  const email = (attrs.email || "").trim();
  if (email) return email.split("@")[0];
  return "Investidor";
}

export default function AppShell({
  children,
  fullBleed = false,
}: {
  children: React.ReactNode;
  // fullBleed: main sem padding e ocupando a altura toda (usado pelo /chat).
  fullBleed?: boolean;
}) {
  const pathname = usePathname();
  const router = useRouter();
  const [notifAberto, setNotifAberto] = useState(false);
  const [perfilAberto, setPerfilAberto] = useState(false);
  const [nomeUsuario, setNomeUsuario] = useState("");
  const [colapsado, setColapsado] = useState(false);
  const [notificacoes, setNotificacoes] = useState<Notificacao[]>([]);
  const [naoLidas, setNaoLidas] = useState(0);
  const notifRef = useRef<HTMLDivElement>(null);
  const perfilRef = useRef<HTMLDivElement>(null);

  // Nome real do usuário (Cognito) para o topo — substitui o texto fixo.
  useEffect(() => {
    let ativo = true;
    fetchUserAttributes()
      .then((attrs) => {
        if (ativo) setNomeUsuario(primeiroNome(attrs));
      })
      .catch(() => {
        /* sem sessão/atributos: mantém vazio (fallback abaixo) */
      });
    return () => {
      ativo = false;
    };
  }, []);

  // Carrega notificações reais (alertas de mercado + proativos da carteira).
  useEffect(() => {
    let ativo = true;
    listarNotificacoes()
      .then((r) => {
        if (ativo) {
          setNotificacoes(r.notificacoes);
          setNaoLidas(r.nao_lidas);
        }
      })
      .catch(() => {
        /* sem sessão/erro de rede: mantém lista vazia */
      });
    return () => {
      ativo = false;
    };
  }, []);

  // Abre/fecha o sino; ao abrir com pendências, marca as de mercado como lidas.
  function alternarNotif(e: React.MouseEvent) {
    e.stopPropagation();
    const abrindo = !notifAberto;
    setNotifAberto(abrindo);
    setPerfilAberto(false);
    if (abrindo && naoLidas > 0) {
      setNaoLidas(0);
      marcarNotificacoesLidas().catch(() => {
        /* falha ao marcar não é crítica */
      });
    }
  }

  // Limpa a lista exibida e marca as de mercado como lidas no servidor.
  // Alertas proativos podem reaparecer ao recarregar enquanto o problema
  // da carteira existir (são avisos "vivos").
  function marcarComoLidas(e: React.MouseEvent) {
    // "Marcar como lidas": zera o badge de não-lidas e tira o destaque
    // visual das mercado, mas mantém a lista visível — o usuário continua
    // vendo o que já foi lido, útil para conferir alertas passados.
    e.stopPropagation();
    setNaoLidas(0);
    setNotificacoes((atual) => atual.map((n) => ({ ...n, lida: true })));
    marcarNotificacoesLidas().catch(() => {
      /* falha ao marcar não é crítica */
    });
  }

  function limparNotif(e: React.MouseEvent) {
    // "Limpar": apaga as de mercado do storage e registra as proativas
    // como descartadas, para não reaparecerem no próximo fetch — antes
    // elas voltavam porque _alertas_proativos regenera a lista a cada
    // listagem (ver _listar_notificacoes no proxy).
    e.stopPropagation();
    setNotificacoes([]);
    setNaoLidas(0);
    limparNotificacoes().catch(() => {
      /* falha ao limpar não é crítica */
    });
  }

  useEffect(() => {
    function aoClicarFora(e: MouseEvent) {
      if (notifRef.current && !notifRef.current.contains(e.target as Node)) {
        setNotifAberto(false);
      }
      if (perfilRef.current && !perfilRef.current.contains(e.target as Node)) {
        setPerfilAberto(false);
      }
    }
    document.addEventListener("click", aoClicarFora);
    return () => document.removeEventListener("click", aoClicarFora);
  }, []);

  async function sair() {
    try {
      await signOut();
    } finally {
      router.replace("/login");
    }
  }

  return (
    <div className="bg-background text-on-background h-screen overflow-hidden flex flex-col font-body-sm">
      {/* TopNavBar */}
      <header className="shrink-0 bg-surface-container-lowest border-b border-outline-variant top-0 z-50">
        <div className="flex justify-between items-center h-20 px-md w-full max-w-container-max mx-auto">
          <div className="flex items-center gap-2">
            <img alt="GEVI Logo" className="h-8 w-auto object-cover" src={LOGO_SRC} />
          </div>
          <div className="flex items-center gap-4">
            {/* Notificações */}
            <div className="hidden sm:flex items-center gap-2 relative" ref={notifRef}>
              <button
                className="relative p-2 text-on-surface-variant hover:bg-surface-container-high rounded-full transition-colors flex items-center justify-center"
                onClick={alternarNotif}
                aria-label="Notificações"
              >
                <span className="material-symbols-outlined">notifications</span>
                {naoLidas > 0 && (
                  <span className="absolute top-1 right-1 min-w-4 h-4 px-1 flex items-center justify-center rounded-full bg-error text-[10px] font-bold leading-none text-on-error">
                    {naoLidas > 9 ? "9+" : naoLidas}
                  </span>
                )}
              </button>
              <div
                className={`absolute right-0 top-full mt-2 w-80 bg-surface-container-lowest border border-outline-variant rounded-lg shadow-lg z-50 ${notifAberto ? "" : "hidden"}`}
              >
                <div className="p-4 border-b border-outline-variant flex items-center justify-between gap-2">
                  <h3 className="font-bold text-on-surface">Notificações</h3>
                  {notificacoes.length > 0 && (
                    <div className="flex items-center gap-3">
                      {notificacoes.some((n) => !n.lida) && (
                        <button
                          onClick={marcarComoLidas}
                          className="text-xs font-semibold text-primary-container hover:underline"
                        >
                          Marcar como lidas
                        </button>
                      )}
                      <button
                        onClick={limparNotif}
                        className="text-xs font-semibold text-error hover:underline"
                      >
                        Limpar
                      </button>
                    </div>
                  )}
                </div>
                {notificacoes.length === 0 ? (
                  <div className="p-6 text-center text-sm text-on-surface-variant">
                    Sem notificações no momento.
                  </div>
                ) : (
                  <div className="max-h-96 overflow-y-auto">
                    {notificacoes.map((n) => (
                      <div
                        key={n.id}
                        className={`px-4 py-3 border-b border-outline-variant/40 last:border-0 ${
                          n.lida ? "" : "bg-secondary-container/10"
                        }`}
                      >
                        <p className="text-sm font-semibold text-on-surface">
                          {n.titulo}
                        </p>
                        <p className="mt-1 text-xs text-on-surface-variant">
                          {n.mensagem}
                        </p>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>

            {/* Perfil */}
            <div
              className="flex items-center gap-3 pl-4 border-l border-outline-variant cursor-pointer hover:bg-surface-container-high p-2 rounded-lg transition-colors relative"
              ref={perfilRef}
              onClick={(e) => {
                e.stopPropagation();
                setPerfilAberto((v) => !v);
                setNotifAberto(false);
              }}
            >
              <div className="w-10 h-10 rounded-full bg-secondary-container flex items-center justify-center border-2 border-surface">
                <span className="material-symbols-outlined text-on-secondary-container">
                  person
                </span>
              </div>
              <div className="hidden md:block pointer-events-none">
                <span className="block font-label-caps text-label-caps text-on-surface-variant">
                  Meu Perfil
                </span>
                <span className="block font-body-sm text-body-sm font-semibold text-on-surface">
                  {nomeUsuario || "Investidor"}
                </span>
              </div>
              <div
                className={`absolute right-0 top-full mt-2 w-48 bg-surface-container-lowest border border-outline-variant rounded-lg shadow-lg z-50 ${perfilAberto ? "" : "hidden"}`}
              >
                <div className="py-1">
                  <Link
                    className="block px-4 py-2 text-sm text-on-surface hover:bg-surface-container-high transition-colors"
                    href="/minha-conta"
                  >
                    Minha conta
                  </Link>
                  <button
                    className="block w-full text-left px-4 py-2 text-sm text-error hover:bg-error-container/10 transition-colors"
                    onClick={sair}
                  >
                    Sair
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </header>

      {/* Layout: SideNav (encostado na borda esquerda real da janela) +
          conteúdo (este sim respeita o max-width centralizado). */}
      <div className="flex flex-1 min-h-0 w-full">
        <aside
          className={`hidden lg:flex flex-col p-4 h-full bg-primary-container z-40 border-r border-white/10 overflow-y-auto relative transition-[width] duration-200 shrink-0 ${
            colapsado ? "w-20" : "w-64"
          }`}
        >
          {/* Botão de recolher/expandir o menu */}
          <button
            onClick={() => setColapsado((v) => !v)}
            aria-label={colapsado ? "Expandir menu" : "Recolher menu"}
            title={colapsado ? "Expandir menu" : "Recolher menu"}
            className={`flex items-center text-white hover:bg-white/10 rounded-lg p-2 mb-2 transition-colors ${
              colapsado ? "justify-center" : "justify-end"
            }`}
          >
            <span className="material-symbols-outlined">
              {colapsado ? "chevron_right" : "chevron_left"}
            </span>
          </button>
          <nav className="flex flex-col gap-2 flex-1">
            <div className="flex flex-col gap-1">
              {NAV.map((item) => {
                const ativo = pathname === item.href;
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    title={item.label}
                    className={
                      (ativo
                        ? "flex items-center gap-3 bg-secondary-container text-on-secondary-container rounded-lg p-3 font-bold shadow-lg text-white"
                        : "flex items-center gap-3 p-3 hover:bg-white/10 rounded-lg transition-all text-white") +
                      (colapsado ? " justify-center" : "")
                    }
                  >
                    <span className="material-symbols-outlined text-white">
                      {item.icon}
                    </span>
                    {!colapsado && (
                      <span className="font-medium text-white">{item.label}</span>
                    )}
                  </Link>
                );
              })}
            </div>
          </nav>
        </aside>

        <main
          className={
            fullBleed
              ? "flex-1 min-w-0 flex flex-col min-h-0"
              : "flex-1 min-w-0 p-md md:p-xl overflow-y-auto"
          }
        >
          {fullBleed ? (
            children
          ) : (
            <div className="w-full max-w-container-max mx-auto flex flex-col gap-xl">
              {children}
            </div>
          )}
        </main>
      </div>

      {/* Aviso global (plataforma de apoio, sem transações) */}
      <footer className="shrink-0 border-t border-outline-variant bg-surface-container-lowest px-md py-2">
        <Disclaimer />
      </footer>
    </div>
  );
}
