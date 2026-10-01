"use client";

// Onboarding — questionário de suitability (Agente_Perfil).
// Mostrado no 1º login (quem ainda não tem perfil). As 6 respostas (1–3 pts)
// são avaliadas pelo Agente_Perfil (classificação determinística + justificativa
// por IA) e o perfil é persistido. Ao final, exibe o resultado e leva ao dashboard.
import { useState } from "react";
import { useRouter } from "next/navigation";
import AuthGuard from "@/components/AuthGuard";
import Disclaimer from "@/components/Disclaimer";
import { avaliarPerfil, type ResultadoPerfil } from "@/lib/api";

type Opcao = { texto: string; pontos: number };
type Pergunta = { id: string; titulo: string; opcoes: Opcao[] };

const PERGUNTAS: Pergunta[] = [
  {
    id: "p1",
    titulo: "Qual é o seu principal objetivo ao investir?",
    opcoes: [
      { texto: "Preservar meu patrimônio e evitar perdas", pontos: 1 },
      { texto: "Equilibrar segurança e crescimento", pontos: 2 },
      { texto: "Buscar o maior crescimento possível, aceitando maiores riscos", pontos: 3 },
    ],
  },
  {
    id: "p2",
    titulo: "Por quanto tempo pretende manter a maior parte dos investimentos?",
    opcoes: [
      { texto: "Menos de 1 ano", pontos: 1 },
      { texto: "Entre 1 e 3 anos", pontos: 2 },
      { texto: "Mais de 3 anos", pontos: 3 },
    ],
  },
  {
    id: "p3",
    titulo: "Se sua carteira caísse 20% em um único mês, o que você faria?",
    opcoes: [
      { texto: "Resgataria meus investimentos para evitar novas perdas", pontos: 1 },
      { texto: "Manteria os investimentos e aguardaria a recuperação", pontos: 2 },
      { texto: "Manteria e/ou investiria mais, aproveitando os preços menores", pontos: 3 },
    ],
  },
  {
    id: "p4",
    titulo: "Qual é o seu nível de experiência com investimentos?",
    opcoes: [
      { texto: "Nenhuma ou apenas poupança", pontos: 1 },
      { texto: "Renda fixa e/ou fundos de investimento", pontos: 2 },
      { texto: "Ações, FIIs, ETFs ou outros de renda variável", pontos: 3 },
    ],
  },
  {
    id: "p5",
    titulo: "Que percentual da sua renda mensal consegue destinar aos investimentos?",
    opcoes: [
      { texto: "Até 10%", pontos: 1 },
      { texto: "Entre 10% e 30%", pontos: 2 },
      { texto: "Mais de 30%", pontos: 3 },
    ],
  },
  {
    id: "p6",
    titulo: "Como você avalia seu conhecimento sobre investimentos?",
    opcoes: [
      { texto: "Básico", pontos: 1 },
      { texto: "Intermediário", pontos: 2 },
      { texto: "Avançado", pontos: 3 },
    ],
  },
];

export default function OnboardingPage() {
  const router = useRouter();
  const [respostas, setRespostas] = useState<Record<string, number>>({});
  const [carregando, setCarregando] = useState(false);
  const [erro, setErro] = useState<string | null>(null);
  const [resultado, setResultado] = useState<ResultadoPerfil | null>(null);

  const completo = PERGUNTAS.every((p) => respostas[p.id]);

  async function enviar() {
    if (!completo || carregando) return;
    setErro(null);
    setCarregando(true);
    try {
      const r = await avaliarPerfil(respostas);
      setResultado(r);
    } catch (e) {
      setErro(e instanceof Error ? e.message : "Falha ao avaliar o perfil.");
    } finally {
      setCarregando(false);
    }
  }

  return (
    <AuthGuard>
      <div className="min-h-screen bg-background text-on-surface font-body-lg py-xl px-md">
        <div className="max-w-2xl mx-auto">
          {!resultado ? (
            <>
              <div className="mb-lg text-center">
                <h1 className="font-headline-lg text-headline-lg text-primary-container mb-xs">
                  Vamos descobrir seu perfil de investidor
                </h1>
                <p className="font-body-lg text-body-lg text-on-surface-variant">
                  Responda 6 perguntas rápidas. Nossa IA vai analisar suas
                  respostas e identificar o perfil mais adequado a você.
                </p>
              </div>

              <div className="space-y-md">
                {PERGUNTAS.map((p, i) => (
                  <div
                    key={p.id}
                    className="bg-surface-container-lowest border border-outline-variant rounded-xl p-md"
                  >
                    <h3 className="font-headline-md text-headline-md text-on-surface mb-sm">
                      {i + 1}. {p.titulo}
                    </h3>
                    <div className="space-y-xs">
                      {p.opcoes.map((o) => {
                        const marcado = respostas[p.id] === o.pontos;
                        return (
                          <label
                            key={o.pontos}
                            className={`flex items-center gap-sm p-sm rounded-lg border cursor-pointer transition-colors ${
                              marcado
                                ? "border-primary-container bg-primary-container/10"
                                : "border-outline-variant hover:bg-surface-container"
                            }`}
                          >
                            <input
                              type="radio"
                              name={p.id}
                              className="accent-primary-container"
                              checked={marcado}
                              onChange={() =>
                                setRespostas((r) => ({ ...r, [p.id]: o.pontos }))
                              }
                            />
                            <span className="font-body-lg text-body-lg">{o.texto}</span>
                          </label>
                        );
                      })}
                    </div>
                  </div>
                ))}
              </div>

              {erro && (
                <p className="mt-md font-body-sm text-body-sm text-error" role="alert">
                  {erro}
                </p>
              )}

              <button
                onClick={enviar}
                disabled={!completo || carregando}
                className="mt-lg w-full bg-primary-container text-on-primary font-headline-md text-headline-md py-3 rounded-lg hover:bg-on-primary-fixed-variant transition-colors shadow-sm disabled:opacity-50"
              >
                {carregando ? "Analisando seu perfil..." : "Descobrir meu perfil"}
              </button>
            </>
          ) : (
            <ResultadoView resultado={resultado} aoContinuar={() => router.push("/dashboard")} />
          )}
          <Disclaimer className="mt-lg" />
        </div>
      </div>
    </AuthGuard>
  );
}

const COR_PERFIL: Record<string, string> = {
  conservador: "bg-secondary-container text-on-secondary-container",
  moderado: "bg-primary-container text-on-primary",
  arrojado: "bg-tertiary-container text-on-tertiary-container",
};

function ResultadoView({
  resultado,
  aoContinuar,
}: {
  resultado: ResultadoPerfil;
  aoContinuar: () => void;
}) {
  const cor = COR_PERFIL[resultado.perfil] ?? "bg-primary-container text-on-primary";
  return (
    <div className="bg-surface-container-lowest border border-outline-variant rounded-xl p-lg shadow-sm">
      <div className="text-center mb-lg">
        <p className="font-label-caps text-label-caps text-on-surface-variant uppercase tracking-wider mb-xs">
          Seu perfil de investidor
        </p>
        <span
          className={`inline-block px-md py-xs rounded-full font-headline-md text-headline-md capitalize ${cor}`}
        >
          {resultado.perfil}
        </span>
        <p className="mt-sm font-body-sm text-body-sm text-on-surface-variant">
          Pontuação: {resultado.pontuacao}/{resultado.pontuacao_maxima}
        </p>
      </div>

      <div className="bg-surface-container rounded-lg p-md mb-md">
        <h3 className="font-headline-md text-headline-md text-primary-container mb-xs">
          Por que este perfil?
        </h3>
        <p className="font-body-lg text-body-lg text-on-surface whitespace-pre-wrap">
          {resultado.justificativa}
        </p>
        {resultado.regra_consistencia_aplicada && (
          <p className="mt-sm font-body-sm text-body-sm text-tertiary">
            Ajuste aplicado: sua baixa tolerância a perdas limitou a classificação,
            mesmo com pontuação alta.
          </p>
        )}
      </div>

      <div className="mb-lg">
        <h3 className="font-headline-md text-headline-md text-on-surface mb-sm">
          Suas características
        </h3>
        <div className="space-y-xs">
          {resultado.caracteristicas.map((c) => (
            <div
              key={c.dimensao}
              className="flex justify-between gap-sm border-b border-outline-variant pb-xs"
            >
              <span className="font-body-sm text-body-sm text-on-surface-variant">
                {c.dimensao}
              </span>
              <span className="font-body-sm text-body-sm text-on-surface text-right">
                {c.valor}
              </span>
            </div>
          ))}
        </div>
      </div>

      <button
        onClick={aoContinuar}
        className="w-full bg-primary-container text-on-primary font-headline-md text-headline-md py-3 rounded-lg hover:bg-on-primary-fixed-variant transition-colors shadow-sm"
      >
        Ir para o dashboard
      </button>
    </div>
  );
}
