"use client";

// Dashboard / Início — reorganizado a pedido: o topo da tela passa a mostrar
// o resumo da carteira (total investido, perfil de risco, alocação) e a
// parte inferior mostra os indicadores macro (Selic/IPCA/Dólar/CDI). No meio,
// o gráfico de evolução dá lugar a um gráfico de pizza da alocação por
// categoria, e o painel de "Alertas Inteligentes" dá lugar à listagem dos
// investimentos por categoria com o valor de cada um.
import { useEffect, useState } from "react";
import Link from "next/link";
import AuthGuard from "@/components/AuthGuard";
import AppShell from "@/components/AppShell";
import GraficoAlocacao, { type FatiaAlocacao } from "@/components/GraficoAlocacao";
import {
  consultarIndicadores,
  listarPortfolio,
  lerPerfil,
  type Indicador,
  type AtivoCarteira,
} from "@/lib/api";

// Rótulo amigável (PT) para a tolerância a risco do perfil.
const LABEL_RISCO: Record<string, string> = {
  CONSERVADOR: "Conservador",
  MODERADO: "Moderado",
  ARROJADO: "Arrojado",
};

const LABEL_TIPO_ATIVO: Record<string, string> = {
  RENDA_FIXA: "Renda Fixa",
  RENDA_VARIAVEL: "Renda Variável",
  FII: "Fundos Imobiliários",
  CRIPTO: "Cripto",
};

function moedaBR(n: number): string {
  return n.toLocaleString("pt-BR", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

// Data da última alteração da carteira, em termos relativos (ex.: "hoje",
// "há 3 dias") — mais legível que uma data crua no resumo. Sem histórico de
// eventos: só a data do salvamento mais recente (decisão: manter simples,
// sem tabela nova — ver docs/decisoes-agentes-e-latencia.md).
function ultimaAlteracaoRelativa(iso: string): string {
  if (!iso) return "—";
  const data = new Date(iso);
  if (Number.isNaN(data.getTime())) return "—";
  const dias = Math.floor((Date.now() - data.getTime()) / 86_400_000);
  if (dias <= 0) return "hoje";
  if (dias === 1) return "ontem";
  if (dias < 30) return `há ${dias} dias`;
  return data.toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit", year: "numeric" });
}

// Indicadores exibidos e como formatar cada um. Fallback = valor de exemplo do
// mockup, usado enquanto carrega ou se o indicador estiver indisponível.
type ConfigMetrica = {
  id: string;
  label: string;
  simbolo: string;
  fallback: string;
  moeda?: boolean;
};

const CONFIG_METRICAS: ConfigMetrica[] = [
  { id: "SELIC", label: "Selic", simbolo: "%", fallback: "10,50%" },
  { id: "IPCA", label: "IPCA", simbolo: "%", fallback: "4,20%" },
  { id: "DOLAR", label: "Dólar", simbolo: "$", fallback: "R$ 5,12", moeda: true },
  { id: "CDI", label: "CDI", simbolo: "%", fallback: "10,40%" },
];

function num(n: number): string {
  return n.toFixed(2).replace(".", ",");
}

function formatarValor(cfg: ConfigMetrica, ind?: Indicador): string {
  if (!ind || ind.disponivel === false) return cfg.fallback;
  if (cfg.moeda) {
    return ind.valor != null ? `R$ ${num(ind.valor)}` : cfg.fallback;
  }
  // Indicadores de taxa: exibe em base anual (% a.a.), coerente com a análise da
  // IA (ex.: CDI vem em % a.d.; usamos o valor anualizado quando disponível).
  const v = ind.valor_anualizado ?? ind.valor;
  return v != null ? `${num(v)}%` : cfg.fallback;
}

export default function DashboardPage() {
  const [porId, setPorId] = useState<Record<string, Indicador>>({});
  const [ativos, setAtivos] = useState<AtivoCarteira[]>([]);
  const [totalCarteira, setTotalCarteira] = useState(0);
  const [risco, setRisco] = useState<string>("");
  const [atualizadaEm, setAtualizadaEm] = useState("");
  const [ultimaAlteracaoDescricao, setUltimaAlteracaoDescricao] = useState("");

  useEffect(() => {
    let ativo = true;
    consultarIndicadores(CONFIG_METRICAS.map((m) => m.id))
      .then((lista) => {
        if (!ativo) return;
        const mapa: Record<string, Indicador> = {};
        for (const ind of lista) mapa[ind.indicator_id] = ind;
        setPorId(mapa);
      })
      .catch(() => {
        /* mantém os fallbacks do mockup em caso de falha/token ausente */
      });
    // Carteira real (alocação + total) e perfil (risco) para o resumo.
    listarPortfolio()
      .then((p) => {
        if (!ativo) return;
        setAtivos(p.ativos);
        setTotalCarteira(p.valor_total);
        setAtualizadaEm(p.atualizadaEm);
        setUltimaAlteracaoDescricao(p.ultimaAlteracaoDescricao);
      })
      .catch(() => {});
    lerPerfil()
      .then((perfil) => ativo && setRisco(perfil?.tolerancia_risco ?? ""))
      .catch(() => {});
    return () => {
      ativo = false;
    };
  }, []);

  const temCarteira = ativos.length > 0;
  // Agrega a alocação por tipo de ativo a partir da carteira real.
  const porTipoValor = ativos.reduce<Record<string, number>>((acc, a) => {
    acc[a.tipo_ativo] = (acc[a.tipo_ativo] ?? 0) + a.valor;
    return acc;
  }, {});
  const alocacoes: FatiaAlocacao[] = Object.entries(porTipoValor)
    .map(([tipo, valor]) => ({
      tipo,
      label: LABEL_TIPO_ATIVO[tipo] ?? tipo,
      valor,
      pct: totalCarteira > 0 ? (valor / totalCarteira) * 100 : 0,
    }))
    .sort((a, b) => b.pct - a.pct);

  // Investimentos individuais agrupados por categoria, com o valor de cada
  // ativo (para a listagem que substitui os "Alertas Inteligentes").
  const porCategoria = new Map<string, AtivoCarteira[]>();
  for (const a of ativos) {
    const lista = porCategoria.get(a.tipo_ativo) ?? [];
    lista.push(a);
    porCategoria.set(a.tipo_ativo, lista);
  }
  const categoriasOrdenadas = Array.from(porCategoria.entries()).sort(
    ([, la], [, lb]) =>
      lb.reduce((s, a) => s + a.valor, 0) - la.reduce((s, a) => s + a.valor, 0),
  );

  const metricas = CONFIG_METRICAS.map((cfg) => ({
    label: cfg.label,
    simbolo: cfg.simbolo,
    valor: formatarValor(cfg, porId[cfg.id]),
  }));

  return (
    <AuthGuard>
      <AppShell>
        {/* Welcome Header */}
        <section className="flex flex-col gap-2">
          <h1 className="font-headline-lg-mobile md:font-headline-lg text-headline-lg-mobile md:text-headline-lg text-on-surface">
            Bem vindo(a) de volta
          </h1>
          <p className="font-body-lg text-body-lg text-on-surface-variant">
            Acompanhe o mercado, seus indicadores e a performance da sua carteira
          </p>
        </section>

        {/* Top: Resumo da Carteira (dados reais) */}
        <section className="bg-surface-container-low rounded-xl p-lg shadow-sm border border-outline-variant/30 flex flex-col gap-6">
          <div className="flex justify-between items-center">
            <h3 className="font-headline-md text-headline-md font-bold text-on-surface">
              Resumo da Carteira
            </h3>
            <Link
              className="flex items-center gap-1 text-secondary font-label-caps text-label-caps hover:underline"
              href="/investimentos"
            >
              Ver carteira completa{" "}
              <span className="material-symbols-outlined text-sm">arrow_forward</span>
            </Link>
          </div>

          {!temCarteira ? (
            <div className="flex flex-col items-center text-center gap-3 py-md">
              <span className="material-symbols-outlined text-4xl text-on-surface-variant">
                account_balance_wallet
              </span>
              <p className="font-body-lg text-body-lg text-on-surface-variant">
                Você ainda não cadastrou sua carteira.
              </p>
              <Link
                href="/investimentos"
                className="bg-primary-container text-on-primary font-headline-md text-headline-md py-sm px-xl rounded-lg hover:bg-on-primary-fixed-variant transition-colors shadow-sm"
              >
                Cadastrar carteira
              </Link>
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-3 gap-md">
              {/* Total Investido (real) */}
              <div className="bg-primary-container rounded-xl p-md flex items-center gap-4 shadow-sm">
                <div className="w-14 h-14 rounded-full bg-secondary-container flex items-center justify-center">
                  <span
                    className="material-symbols-outlined text-on-secondary-container text-2xl"
                    style={{ fontVariationSettings: "'FILL' 1" }}
                  >
                    account_balance
                  </span>
                </div>
                <div>
                  <span className="block font-label-caps text-label-caps text-on-primary-container/80 uppercase">
                    Total Investido
                  </span>
                  <span className="block font-data-display text-data-display text-on-primary leading-none mt-1">
                    R$ {moedaBR(totalCarteira)}
                  </span>
                </div>
              </div>

              {/* Risco (perfil real) */}
              <div className="bg-primary-container rounded-xl p-md flex items-center gap-4 shadow-sm">
                <div className="w-14 h-14 rounded-full bg-secondary-container flex items-center justify-center">
                  <span
                    className="material-symbols-outlined text-on-secondary-container text-2xl"
                    style={{ fontVariationSettings: "'FILL' 1" }}
                  >
                    shield
                  </span>
                </div>
                <div>
                  <span className="block font-label-caps text-label-caps text-on-primary-container/80 uppercase">
                    Perfil de Risco
                  </span>
                  <span className="block font-data-display text-data-display text-on-primary leading-none mt-1">
                    {LABEL_RISCO[risco] ?? "—"}
                  </span>
                </div>
              </div>

              {/* Última alteração da carteira (substitui o resumo de
                  alocação, já coberto pelo gráfico de pizza abaixo). Mostra
                  quando E o que mudou (diff do salvamento mais recente — sem
                  histórico completo, ver ultimaAlteracaoDescricao no proxy). */}
              <div className="bg-primary-container rounded-xl p-md flex items-center gap-4 shadow-sm overflow-hidden">
                <div className="w-14 h-14 rounded-full bg-secondary-container flex items-center justify-center shrink-0">
                  <span
                    className="material-symbols-outlined text-on-secondary-container text-2xl"
                    style={{ fontVariationSettings: "'FILL' 1" }}
                  >
                    history
                  </span>
                </div>
                <div className="min-w-0">
                  <span className="block font-headline-md text-headline-md font-bold text-on-primary leading-tight">
                    Última alteração: {temCarteira ? ultimaAlteracaoRelativa(atualizadaEm) : "—"}
                  </span>
                  {ultimaAlteracaoDescricao && (
                    <span className="block whitespace-pre-line text-xs text-on-primary-container/80 mt-1 leading-relaxed">
                      {ultimaAlteracaoDescricao}
                    </span>
                  )}
                </div>
              </div>
            </div>
          )}
        </section>

        {/* Middle Section: Alocação por categoria (gráfico) & Investimentos por categoria (lista) */}
        <section className="grid grid-cols-1 xl:grid-cols-3 gap-md">
          {/* Gráfico de pizza da alocação por categoria */}
          <div className="xl:col-span-2 flex flex-col gap-4">
            <h3 className="font-headline-md text-headline-md font-bold text-on-surface mb-2">
              Alocação por Categoria
            </h3>
            <GraficoAlocacao fatias={alocacoes} />
          </div>

          {/* Investimentos por categoria, com o valor de cada ativo. Mesma
              estrutura da coluna ao lado (título fora do card + card com
              altura h-96) para as duas colunas ficarem com a mesma altura. */}
          <div className="flex flex-col gap-4">
            <h3 className="font-headline-md text-headline-md font-bold text-on-surface mb-2">
              Investimentos por Categoria
            </h3>
            <div className="bg-surface-container-low rounded-xl p-lg h-96 flex flex-col gap-6 shadow-sm border border-outline-variant/30 relative overflow-hidden">
              <div className="absolute -right-10 -top-10 w-32 h-32 bg-secondary-fixed/20 rounded-full blur-2xl pointer-events-none" />
              {!temCarteira ? (
                <p className="font-body-sm text-body-sm text-on-surface-variant relative z-10">
                  Cadastre ativos na sua carteira para ver a listagem aqui.
                </p>
              ) : (
                <div className="flex flex-col gap-5 relative z-10 overflow-y-auto">
                  {categoriasOrdenadas.map(([tipo, itens]) => (
                    <div key={tipo} className="flex flex-col gap-2">
                      <h4 className="font-body-sm text-body-sm font-bold text-on-surface">
                        {LABEL_TIPO_ATIVO[tipo] ?? tipo}
                      </h4>
                      <div className="flex flex-col gap-1.5">
                        {itens.map((a) => (
                          <div
                            key={a.assetId}
                            className="flex items-center justify-between gap-3 text-[13px]"
                          >
                            <span className="text-on-surface-variant truncate">
                              {a.assetId}
                            </span>
                            <span className="text-on-surface font-semibold shrink-0">
                              R$ {moedaBR(a.valor)}
                            </span>
                          </div>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </section>

        {/* Bottom: Indicadores econômicos */}
        <section className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-md">
          {metricas.map((m) => (
            <div
              key={m.label}
              className="bg-primary-container rounded-xl p-md flex flex-col gap-4 shadow-sm hover:shadow-[0_4px_20px_rgba(0,26,63,0.1)] transition-shadow"
            >
              <div className="flex items-start justify-between">
                <div className="flex items-center gap-3">
                  <div className="w-12 h-12 rounded-full bg-secondary-container flex items-center justify-center">
                    <span className="font-data-display text-data-display text-on-secondary-container">
                      {m.simbolo}
                    </span>
                  </div>
                  <div>
                    <span className="block font-label-caps text-label-caps text-on-primary-container/80 uppercase tracking-wider">
                      {m.label}
                    </span>
                    <span className="block font-data-display text-data-display text-on-primary">
                      {m.valor}
                    </span>
                  </div>
                </div>
              </div>
            </div>
          ))}
        </section>
      </AppShell>
    </AuthGuard>
  );
}
