"use client";

// Gráfico de Evolução dos Indicadores — chips (Selic, IPCA, CDI, Dólar) + linha
// reagindo à seleção. As séries são REAIS (BCB/SGS, mensais), recebidas por
// ``series``; o eixo Y auto-escala por série. Sem série, mostra estado vazio.
import { useMemo, useState } from "react";

export type Ponto = { rotulo: string; valor: number };
export type SeriesPorIndicador = Record<string, Ponto[]>;

const INDICADORES = ["Selic", "IPCA", "CDI", "Dólar"] as const;

function maxAgradavel(max: number): number {
  if (max <= 0) return 1;
  const grandeza = Math.pow(10, Math.floor(Math.log10(max)));
  return Math.ceil(max / (grandeza / 2)) * (grandeza / 2);
}

function formatar(n: number): string {
  if (n >= 1000) return `${Math.round(n / 1000)} mil`;
  return Number.isInteger(n) ? String(n) : n.toFixed(1).replace(".", ",");
}

export default function GraficoEvolucao({
  series = {},
}: {
  series?: SeriesPorIndicador;
}) {
  const [indicador, setIndicador] = useState<string>("Selic");
  const dados = series[indicador] ?? [];
  const semDados = dados.length === 0;

  const { caminho, pontos, rotulosY, rotulosX } = useMemo(() => {
    const valores = dados.map((d) => d.valor);
    const topo = maxAgradavel(Math.max(1, ...valores));
    const n = dados.length;
    // Insets (padding) para a linha não encostar nas bordas do card e os
    // círculos/traço não vazarem para fora da SVG.
    const padX = 3;
    const padY = 14;
    const larg = 100 - 2 * padX;
    const alt = 100 - 2 * padY;
    const coords = dados.map((d, i) => ({
      x: padX + (n > 1 ? i / (n - 1) : 0) * larg,
      y: padY + (1 - d.valor / topo) * alt,
    }));
    const caminho = coords
      .map((c, i) => `${i === 0 ? "M" : "L"} ${c.x.toFixed(2)},${c.y.toFixed(2)}`)
      .join(" ");
    // 5 rótulos do eixo Y, de ``topo`` até 0.
    const rotulosY = Array.from({ length: 5 }, (_, i) =>
      formatar((topo * (4 - i)) / 4),
    );
    return {
      caminho,
      pontos: coords,
      rotulosY,
      rotulosX: dados.map((d) => d.rotulo),
    };
  }, [dados]);

  return (
    <div className="flex flex-col gap-4">
      {/* Chips de indicador */}
      <div className="flex flex-wrap gap-2">
        {INDICADORES.map((ind) => {
          const ativo = ind === indicador;
          return (
            <button
              key={ind}
              type="button"
              onClick={() => setIndicador(ind)}
              className={
                ativo
                  ? "px-4 py-1.5 rounded-full font-label-caps text-label-caps bg-secondary-container text-on-secondary-container shadow-sm transition-colors"
                  : "px-4 py-1.5 rounded-full font-label-caps text-label-caps bg-secondary text-on-secondary hover:bg-secondary-container hover:text-on-secondary-container transition-colors"
              }
              aria-pressed={ativo}
            >
              {ind}
            </button>
          );
        })}
      </div>

      {/* Card do gráfico */}
      <div className="bg-primary-container rounded-xl p-md h-80 relative shadow-sm border border-outline-variant/10 flex flex-col">
        {semDados && (
          <div className="absolute inset-0 z-10 flex items-center justify-center text-on-primary-container/70 text-sm">
            Dados históricos indisponíveis no momento.
          </div>
        )}
        <div className="absolute left-4 top-4 bottom-8 flex flex-col justify-between text-[10px] text-on-primary-container/60 font-mono">
          {rotulosY.map((r, i) => (
            <span key={i}>{r}</span>
          ))}
        </div>
        <div className="ml-8 flex-1 relative w-full h-full border-b border-on-primary-container/20">
          {/* Grid lines */}
          <div className="absolute inset-0 flex flex-col justify-between pointer-events-none">
            {Array.from({ length: 5 }).map((_, i) => (
              <div key={i} className="w-full h-[1px] bg-on-primary-container/10" />
            ))}
          </div>
          {/* Linha do indicador selecionado */}
          <svg
            className="absolute inset-0 w-full h-full"
            preserveAspectRatio="none"
            viewBox="0 0 100 100"
          >
            <path
              className="drop-shadow-[0_2px_4px_rgba(186,26,26,0.3)]"
              d={caminho}
              fill="none"
              stroke="#ba1a1a"
              strokeWidth="1.5"
              vectorEffect="non-scaling-stroke"
            />
            {pontos.map((p, i) => (
              <circle key={i} cx={p.x} cy={p.y} fill="#ba1a1a" r="1" />
            ))}
          </svg>
        </div>
        {/* X Axis labels */}
        <div className="ml-8 mt-2 flex justify-between text-[10px] text-on-primary-container/60 font-mono pr-4">
          {rotulosX.map((r, i) => (
            <span key={i}>{r}</span>
          ))}
        </div>
      </div>
    </div>
  );
}
