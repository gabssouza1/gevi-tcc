"use client";

// Minha Carteira — cadastro manual dos ativos do investidor. Os ativos são
// gravados na tabela dev-Portfolios (via proxy: acao salvar_portfolio) e lidos
// de volta pela tool analisar_portfolio que a IA usa. O percentual de cada
// ativo é derivado do valor (participação na carteira).
import { useEffect, useMemo, useState } from "react";
import AuthGuard from "@/components/AuthGuard";
import AppShell from "@/components/AppShell";
import { listarPortfolio, salvarPortfolio } from "@/lib/api";

const TIPOS = [
  { valor: "RENDA_FIXA", label: "Renda Fixa" },
  { valor: "RENDA_VARIAVEL", label: "Renda Variável" },
  { valor: "FII", label: "Fundos Imobiliários" },
  { valor: "CRIPTO", label: "Cripto" },
] as const;

type Linha = { id: string; assetId: string; tipo_ativo: string; valor: string };

// Cada linha recebe um id estável (evita bugs de reconciliação do React ao
// adicionar/remover ativos — a causa do bug de "adicionar ativo").
let _seq = 0;
function novaLinha(assetId = "", tipo_ativo = "RENDA_FIXA", valor = ""): Linha {
  _seq += 1;
  const id =
    typeof crypto !== "undefined" && crypto.randomUUID
      ? crypto.randomUUID()
      : `linha-${_seq}`;
  return { id, assetId, tipo_ativo, valor };
}

function moeda(n: number): string {
  return n.toLocaleString("pt-BR", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function paraNumero(v: string): number {
  const n = Number(String(v).replace(",", "."));
  return Number.isFinite(n) && n > 0 ? n : 0;
}

const INPUT =
  "w-full bg-surface-container-lowest border border-outline-variant rounded-lg py-xs px-sm text-body-sm font-body-sm focus:outline-none focus:ring-2 focus:ring-secondary-container focus:border-transparent text-on-surface";

export default function InvestimentosPage() {
  const [linhas, setLinhas] = useState<Linha[]>([]);
  const [carregando, setCarregando] = useState(true);
  const [salvando, setSalvando] = useState(false);
  const [erro, setErro] = useState("");
  const [okMsg, setOkMsg] = useState("");

  useEffect(() => {
    let ativo = true;
    listarPortfolio()
      .then((p) => {
        if (!ativo) return;
        setLinhas(
          p.ativos.map((a) => novaLinha(a.assetId, a.tipo_ativo, String(a.valor))),
        );
      })
      .catch(() => {
        /* sem carteira/sessão: começa vazio */
      })
      .finally(() => {
        if (ativo) setCarregando(false);
      });
    return () => {
      ativo = false;
    };
  }, []);

  const totalAtual = useMemo(
    () => linhas.reduce((s, l) => s + paraNumero(l.valor), 0),
    [linhas],
  );

  // Alocação viva por tipo de ativo (recalculada enquanto o usuário edita).
  const porTipo = useMemo(() => {
    const mapa: Record<string, number> = {};
    for (const l of linhas) mapa[l.tipo_ativo] = (mapa[l.tipo_ativo] ?? 0) + paraNumero(l.valor);
    return mapa;
  }, [linhas]);

  function atualizar(id: string, campo: keyof Linha, valor: string) {
    setLinhas((ls) => ls.map((l) => (l.id === id ? { ...l, [campo]: valor } : l)));
    setOkMsg("");
  }

  function adicionar() {
    setLinhas((ls) => [...ls, novaLinha()]);
    setOkMsg("");
  }

  function remover(id: string) {
    setLinhas((ls) => ls.filter((l) => l.id !== id));
    setOkMsg("");
  }

  async function salvar() {
    setErro("");
    setOkMsg("");
    const convertidas = linhas.map((l) => ({
      assetId: l.assetId.trim(),
      tipo_ativo: l.tipo_ativo,
      valor: paraNumero(l.valor),
    }));

    // Linhas totalmente vazias (ex.: "Adicionar ativo" clicado e não usado)
    // são ignoradas — não impedem o salvamento do resto da carteira.
    const payload = convertidas.filter((a) => a.assetId || a.valor > 0);

    // Linhas parcialmente preenchidas (nome sem valor, ou valor sem nome)
    // continuam bloqueando o salvamento, mas agora a mensagem aponta qual
    // ativo precisa de atenção em vez de um erro genérico e fácil de perder.
    const incompleta = payload.find((a) => !a.assetId || a.valor <= 0);
    if (incompleta) {
      setErro(
        incompleta.assetId
          ? `Informe um valor maior que zero para "${incompleta.assetId}".`
          : "Informe o nome do ativo em todas as linhas com valor preenchido.",
      );
      return;
    }
    setSalvando(true);
    try {
      const p = await salvarPortfolio(payload);
      setLinhas(
        p.ativos.map((a) => novaLinha(a.assetId, a.tipo_ativo, String(a.valor))),
      );
      setOkMsg("Carteira salva com sucesso.");
    } catch (e) {
      setErro(e instanceof Error ? e.message : "Não foi possível salvar a carteira.");
    } finally {
      setSalvando(false);
    }
  }

  return (
    <AuthGuard>
      <AppShell>
        <section className="flex flex-col gap-2">
          <h1 className="font-headline-lg-mobile md:font-headline-lg text-headline-lg-mobile md:text-headline-lg text-on-surface">
            Minha Carteira
          </h1>
          <p className="font-body-lg text-body-lg text-on-surface-variant">
            Cadastre seus ativos para acompanhar a alocação e receber análises da IA.
          </p>
        </section>

        {/* Resumo real */}
        <section className="grid grid-cols-1 sm:grid-cols-2 gap-md">
          <div className="bg-primary-container rounded-xl p-md flex flex-col gap-sm shadow-sm border-t-4 border-secondary-container">
            <span className="font-label-caps text-on-primary-container uppercase">
              Total Investido
            </span>
            <div className="text-data-display font-data-display text-on-primary mt-2">
              R$ {moeda(totalAtual)}
            </div>
          </div>
          <div className="bg-surface-container-lowest rounded-xl p-md flex flex-col gap-sm border border-outline-variant shadow-sm">
            <span className="font-label-caps text-on-surface-variant uppercase">
              Ativos na Carteira
            </span>
            <div className="text-data-display font-data-display text-primary mt-2">
              {linhas.length}
            </div>
          </div>
        </section>

        {/* Alocação por tipo (viva) */}
        {totalAtual > 0 && (
          <section className="bg-surface-container-low rounded-xl p-lg border border-outline-variant/30 flex flex-col gap-4">
            <h2 className="font-headline-md text-headline-md font-bold text-on-surface">
              Alocação por Tipo
            </h2>
            <div className="flex flex-col gap-3">
              {TIPOS.filter((t) => (porTipo[t.valor] ?? 0) > 0).map((t) => {
                const pct = (porTipo[t.valor] / totalAtual) * 100;
                return (
                  <div key={t.valor} className="flex items-center gap-3">
                    <span className="w-40 text-body-sm text-on-surface-variant">
                      {t.label}
                    </span>
                    <div className="flex-1 h-2 bg-surface-variant rounded-full overflow-hidden">
                      <div
                        className="h-full bg-secondary"
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                    <span className="w-28 text-right font-medium text-on-surface">
                      {pct.toFixed(1)}% · R$ {moeda(porTipo[t.valor])}
                    </span>
                  </div>
                );
              })}
            </div>
          </section>
        )}

        {/* Editor de ativos */}
        <section className="bg-surface-container-lowest rounded-xl border border-outline-variant shadow-sm flex flex-col">
          <div className="px-md py-sm border-b border-outline-variant flex justify-between items-center bg-surface rounded-t-xl">
            <h2 className="font-headline-md text-headline-md text-primary">
              Ativos em Carteira
            </h2>
            <button
              onClick={adicionar}
              disabled={carregando}
              className="flex items-center gap-1 px-sm py-xs rounded-full bg-secondary text-on-secondary font-label-caps text-xs hover:opacity-90 disabled:opacity-50"
            >
              <span className="material-symbols-outlined text-sm">add</span>
              Adicionar ativo
            </button>
          </div>

          {carregando ? (
            <div className="py-lg text-center text-on-surface-variant">Carregando…</div>
          ) : linhas.length === 0 ? (
            <div className="py-lg px-md text-center text-on-surface-variant">
              Você ainda não cadastrou ativos. Clique em “Adicionar ativo” para começar.
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left border-collapse">
                <thead>
                  <tr className="border-b border-outline-variant bg-surface-container-low font-label-caps text-on-surface-variant uppercase">
                    <th className="py-sm px-md font-semibold">Ativo</th>
                    <th className="py-sm px-md font-semibold">Categoria</th>
                    <th className="py-sm px-md font-semibold text-right">Valor (R$)</th>
                    <th className="py-sm px-md font-semibold text-right">Alocação</th>
                    <th className="py-sm px-md font-semibold text-right">Remover</th>
                  </tr>
                </thead>
                <tbody className="font-body-sm divide-y divide-outline-variant">
                  {linhas.map((l) => {
                    const v = paraNumero(l.valor);
                    const pct = totalAtual > 0 ? (v / totalAtual) * 100 : 0;
                    return (
                      <tr key={l.id} className="hover:bg-surface-container-high transition-colors">
                        <td className="py-sm px-md">
                          <input
                            className={INPUT}
                            placeholder="Ex.: Tesouro Selic, PETR4"
                            value={l.assetId}
                            onChange={(e) => atualizar(l.id, "assetId", e.target.value)}
                          />
                        </td>
                        <td className="py-sm px-md">
                          <select
                            className={INPUT}
                            value={l.tipo_ativo}
                            onChange={(e) => atualizar(l.id, "tipo_ativo", e.target.value)}
                          >
                            {TIPOS.map((t) => (
                              <option key={t.valor} value={t.valor}>
                                {t.label}
                              </option>
                            ))}
                          </select>
                        </td>
                        <td className="py-sm px-md">
                          <input
                            className={`${INPUT} text-right`}
                            type="number"
                            min="0"
                            step="0.01"
                            placeholder="0,00"
                            value={l.valor}
                            onChange={(e) => atualizar(l.id, "valor", e.target.value)}
                          />
                        </td>
                        <td className="py-sm px-md text-right text-on-surface-variant">
                          {pct.toFixed(1)}%
                        </td>
                        <td className="py-sm px-md text-right">
                          <button
                            onClick={() => remover(l.id)}
                            aria-label="Remover ativo"
                            className="p-1 text-error hover:bg-error-container/10 rounded-full transition-colors"
                          >
                            <span className="material-symbols-outlined">delete</span>
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

          <div className="px-md py-sm border-t border-outline-variant flex flex-col sm:flex-row sm:justify-between sm:items-center gap-3 bg-surface rounded-b-xl">
            <div className="min-h-[20px] text-body-sm">
              {erro && <span className="text-error">{erro}</span>}
              {okMsg && <span className="text-secondary">{okMsg}</span>}
            </div>
            <button
              onClick={salvar}
              disabled={salvando}
              className="bg-primary-container text-on-primary font-headline-md text-headline-md py-sm px-xl rounded-lg hover:bg-on-primary-fixed-variant transition-colors shadow-sm disabled:opacity-60"
            >
              {salvando ? "Salvando…" : "Salvar carteira"}
            </button>
          </div>
        </section>
      </AppShell>
    </AuthGuard>
  );
}
