"use client";

// Gráfico de pizza (SVG puro, sem dependência externa) com a alocação da
// carteira por categoria de investimento. Substitui o gráfico de evolução
// dos indicadores na tela inicial — mostra a composição real da carteira do
// usuário (analisar_portfolio/listarPortfolio), não dados de mercado.
export type FatiaAlocacao = {
  tipo: string;
  label: string;
  valor: number;
  pct: number; // 0–100
};

// Paleta fixa por tipo de ativo, para a legenda/fatias serem consistentes
// entre telas. Ativos fora dessa lista (não deveria ocorrer) caem no cinza.
const CORES: Record<string, string> = {
  RENDA_FIXA: "#004a77",
  RENDA_VARIAVEL: "#ba1a1a",
  FII: "#6d5e0f",
  CRIPTO: "#5e5e99",
};
const COR_FALLBACK = "#79747e";

function corDoTipo(tipo: string): string {
  return CORES[tipo] ?? COR_FALLBACK;
}

function moedaBR(n: number): string {
  return n.toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

// Converte uma lista de fatias (% de 0 a 100) em segmentos de arco SVG,
// desenhados a partir do topo (12h), em sentido horário.
function segmentos(fatias: FatiaAlocacao[]) {
  const raio = 42;
  const cx = 50;
  const cy = 50;
  let anguloAcumulado = -90; // começa no topo
  return fatias.map((f) => {
    const anguloInicial = anguloAcumulado;
    const anguloVarrido = (f.pct / 100) * 360;
    const anguloFinal = anguloInicial + anguloVarrido;
    anguloAcumulado = anguloFinal;
    const rad = (deg: number) => (deg * Math.PI) / 180;
    const x1 = cx + raio * Math.cos(rad(anguloInicial));
    const y1 = cy + raio * Math.sin(rad(anguloInicial));
    const x2 = cx + raio * Math.cos(rad(anguloFinal));
    const y2 = cy + raio * Math.sin(rad(anguloFinal));
    const grandeArco = anguloVarrido > 180 ? 1 : 0;
    // Fatia única (100%): desenha um círculo completo em vez de um arco
    // degenerado (M-L-A com início == fim não renderiza nada).
    const caminho =
      f.pct >= 99.999
        ? `M ${cx} ${cy - raio} A ${raio} ${raio} 0 1 1 ${cx - 0.001} ${cy - raio} Z`
        : `M ${cx} ${cy} L ${x1} ${y1} A ${raio} ${raio} 0 ${grandeArco} 1 ${x2} ${y2} Z`;
    return { ...f, caminho };
  });
}

export default function GraficoAlocacao({
  fatias,
}: {
  fatias: FatiaAlocacao[];
}) {
  const semDados = fatias.length === 0;
  const arcos = segmentos(fatias);
  const total = fatias.reduce((acc, f) => acc + f.valor, 0);

  return (
    <div className="bg-primary-container rounded-xl p-md h-96 relative shadow-sm border border-outline-variant/10 flex flex-col md:flex-row items-center gap-md">
      {semDados ? (
        <div className="absolute inset-0 z-10 flex items-center justify-center text-on-primary-container/70 text-sm text-center px-md">
          Cadastre sua carteira para ver a alocação por categoria.
        </div>
      ) : (
        <>
          <div className="relative shrink-0 w-64 h-64 mx-auto">
            <svg viewBox="0 0 100 100" className="w-full h-full">
              {arcos.map((a) => (
                <path
                  key={a.tipo}
                  d={a.caminho}
                  fill={corDoTipo(a.tipo)}
                  stroke="rgba(255,255,255,0.15)"
                  strokeWidth="0.5"
                />
              ))}
              {/* Furo central (efeito donut) — mesma cor do card (primary-container) */}
              <circle cx="50" cy="50" r="22" fill="#001a3f" />
            </svg>
            <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
              <span className="font-label-caps text-[10px] text-on-primary-container/70 uppercase">
                Total
              </span>
              <span className="font-data-display text-sm text-on-primary text-center px-2 leading-tight">
                R$ {moedaBR(total)}
              </span>
            </div>
          </div>
          <div className="flex flex-col gap-3 w-full overflow-y-auto">
            {fatias.map((f) => (
              <div key={f.tipo} className="flex items-center justify-between gap-3 text-base">
                <div className="flex items-center gap-2.5 min-w-0">
                  <span
                    className="w-3.5 h-3.5 rounded-sm shrink-0"
                    style={{ backgroundColor: corDoTipo(f.tipo) }}
                  />
                  <span className="text-on-primary-container/90 font-medium truncate">
                    {f.label}
                  </span>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <span className="text-on-primary font-bold">{f.pct.toFixed(1)}%</span>
                  <span className="text-on-primary-container/70">
                    R$ {moedaBR(f.valor)}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
