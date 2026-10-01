"use client";

// Configurações — portado do mockup
// sistema/front/configura_es_gevi_menus_interativos/code.html, dentro do
// AppShell + AuthGuard. Os toggles (JS vanilla no mockup) viraram estado React.
// "Avisos de Segurança" fica fixo ligado/desabilitado, como no mockup.
import { useEffect, useState } from "react";
import AuthGuard from "@/components/AuthGuard";
import AppShell from "@/components/AppShell";
import { listarLimiares, salvarLimiares } from "@/lib/api";

type Toggle = {
  id: string;
  titulo: string;
  descricao: string;
  fixo?: boolean;
};

const EMAIL: Toggle[] = [
  {
    id: "email_market",
    titulo: "Alertas de Mercado",
    descricao:
      "Receba atualizações importantes sobre volatilidade e oportunidades.",
  },
  {
    id: "email_portfolio",
    titulo: "Atualizações do Portfólio",
    descricao: "Relatórios semanais sobre o desempenho dos seus investimentos.",
  },
  {
    id: "email_security",
    titulo: "Avisos de Segurança",
    descricao:
      "Notificações sobre logins em novos dispositivos e alterações de conta.",
    fixo: true,
  },
];



function ItemToggle({
  item,
  ligado,
  onToggle,
}: {
  item: Toggle;
  ligado: boolean;
  onToggle: () => void;
}) {
  return (
    <div className="flex items-center justify-between">
      <div>
        <h4 className="font-body-lg text-body-lg font-bold text-on-surface">
          {item.titulo}
        </h4>
        <p className="font-body-sm text-body-sm text-on-surface-variant">
          {item.descricao}
        </p>
      </div>
      <div className="relative inline-block w-12 mr-2 align-middle select-none">
        <input
          className="toggle-checkbox absolute block w-6 h-6 rounded-full bg-white border-4 appearance-none cursor-pointer border-outline-variant top-0 bottom-0 m-auto right-6 z-10 transition-all duration-200"
          id={`toggle_${item.id}`}
          type="checkbox"
          checked={ligado}
          disabled={item.fixo}
          onChange={onToggle}
        />
        <label
          className={`toggle-label block overflow-hidden h-6 rounded-full cursor-pointer transition-colors duration-200 ${
            item.fixo
              ? "bg-primary-container cursor-not-allowed opacity-70"
              : "bg-outline-variant"
          }`}
          htmlFor={`toggle_${item.id}`}
        />
      </div>
    </div>
  );
}

// Indicadores que aceitam limiar de alerta (mesma lista aceita pelo backend).
const INDICADORES_LIMIAR: { id: string; label: string }[] = [
  { id: "SELIC", label: "Selic" },
  { id: "IPCA", label: "IPCA" },
  { id: "DOLAR", label: "Dólar" },
  { id: "CDI", label: "CDI" },
];

export default function ConfiguracoesPage() {
  const [estado, setEstado] = useState<Record<string, boolean>>({
    email_market: true,
    email_portfolio: true,
    email_security: true,
  });

  // Limiares de alerta de mercado (% de variação) por indicador.
  const [limiares, setLimiares] = useState<Record<string, string>>({});
  const [carregandoLimiares, setCarregandoLimiares] = useState(true);
  const [salvandoLimiares, setSalvandoLimiares] = useState(false);
  const [msgLimiares, setMsgLimiares] = useState("");

  useEffect(() => {
    let ativo = true;
    listarLimiares()
      .then((lista) => {
        if (!ativo) return;
        const mapa: Record<string, string> = {};
        for (const l of lista) {
          mapa[l.indicator_id.toUpperCase()] = String(l.limiar_percentual);
        }
        setLimiares(mapa);
      })
      .catch(() => {
        /* sem limiares configurados ainda */
      })
      .finally(() => {
        if (ativo) setCarregandoLimiares(false);
      });
    return () => {
      ativo = false;
    };
  }, []);

  function alternar(id: string) {
    setEstado((s) => ({ ...s, [id]: !s[id] }));
  }

  function alterarLimiar(id: string, valor: string) {
    setMsgLimiares("");
    setLimiares((m) => ({ ...m, [id]: valor }));
  }

  async function salvarLimiaresMercado() {
    setSalvandoLimiares(true);
    setMsgLimiares("");
    try {
      const payload = INDICADORES_LIMIAR.map((ind) => ({
        indicator_id: ind.id,
        limiar_percentual: Number.parseFloat(
          (limiares[ind.id] ?? "").replace(",", "."),
        ),
      })).filter((l) => Number.isFinite(l.limiar_percentual) && l.limiar_percentual > 0);
      const salvos = await salvarLimiares(payload);
      const mapa: Record<string, string> = {};
      for (const l of salvos) mapa[l.indicator_id.toUpperCase()] = String(l.limiar_percentual);
      setLimiares(mapa);
      setMsgLimiares("Limiares salvos com sucesso.");
    } catch {
      setMsgLimiares("Não foi possível salvar os limiares. Tente novamente.");
    } finally {
      setSalvandoLimiares(false);
    }
  }

  return (
    <AuthGuard>
      <AppShell>
        <div className="mb-lg">
          <h1 className="font-headline-lg-mobile md:font-headline-lg text-headline-lg-mobile md:text-headline-lg text-primary-container mb-2">
            Configurações
          </h1>
          <p className="font-body-lg text-body-lg text-on-surface-variant">
            Gerencie suas preferências e configurações de conta.
          </p>
        </div>

        <div className="bg-surface-container-lowest border border-outline-variant rounded-xl p-md shadow-sm">
          <h2 className="font-headline-md text-headline-md text-primary-container mb-lg border-b border-outline-variant pb-xs">
            Notificações
          </h2>

          <div className="space-y-lg">
            {/* Seção: E-mail */}
            <div>
              <h3 className="font-label-caps text-label-caps text-on-surface-variant mb-md uppercase tracking-wider">
                Por E-mail
              </h3>
              <div className="space-y-md">
                {EMAIL.map((item, i) => (
                  <div key={item.id}>
                    <ItemToggle
                      item={item}
                      ligado={estado[item.id]}
                      onToggle={() => alternar(item.id)}
                    />
                    {i < EMAIL.length - 1 && (
                      <hr className="border-outline-variant mt-md" />
                    )}
                  </div>
                ))}
              </div>
            </div>

          </div>

          <div className="mt-xl pt-lg border-t border-outline-variant flex justify-end">
            <button className="bg-primary-container text-on-primary font-body-lg text-body-lg font-bold py-3 px-6 rounded-lg hover:bg-opacity-90 transition-opacity">
              Salvar Alterações
            </button>
          </div>
        </div>

        {/* Alertas de mercado configuráveis por limiar de variação */}
        <div className="bg-surface-container-lowest border border-outline-variant rounded-xl p-md shadow-sm">
          <h2 className="font-headline-md text-headline-md text-primary-container mb-2 border-b border-outline-variant pb-xs">
            Alertas de mercado
          </h2>
          <p className="font-body-sm text-body-sm text-on-surface-variant mb-lg">
            Defina a variação mínima (%) para receber um alerta quando um
            indicador se mover. Deixe em branco para não receber alertas daquele
            indicador.
          </p>

          {carregandoLimiares ? (
            <p className="font-body-sm text-body-sm text-on-surface-variant">
              Carregando limiares...
            </p>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-md">
              {INDICADORES_LIMIAR.map((ind) => (
                <div key={ind.id} className="flex items-center justify-between gap-4">
                  <label
                    htmlFor={`limiar_${ind.id}`}
                    className="font-body-lg text-body-lg font-bold text-on-surface"
                  >
                    {ind.label}
                  </label>
                  <div className="relative w-28">
                    <input
                      id={`limiar_${ind.id}`}
                      type="number"
                      min="0"
                      step="0.1"
                      inputMode="decimal"
                      placeholder="—"
                      value={limiares[ind.id] ?? ""}
                      onChange={(e) => alterarLimiar(ind.id, e.target.value)}
                      className="w-full rounded-lg border border-outline-variant bg-surface-container-lowest px-3 py-2 pr-7 text-right text-on-surface focus:border-primary-container focus:outline-none"
                    />
                    <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-on-surface-variant">
                      %
                    </span>
                  </div>
                </div>
              ))}
            </div>
          )}

          {msgLimiares && (
            <p
              className={`mt-md font-body-sm text-body-sm ${
                msgLimiares.startsWith("Não")
                  ? "text-error"
                  : "text-on-surface-variant"
              }`}
            >
              {msgLimiares}
            </p>
          )}

          <div className="mt-xl pt-lg border-t border-outline-variant flex justify-end">
            <button
              onClick={salvarLimiaresMercado}
              disabled={salvandoLimiares || carregandoLimiares}
              className="bg-primary-container text-on-primary font-body-lg text-body-lg font-bold py-3 px-6 rounded-lg hover:bg-opacity-90 transition-opacity disabled:opacity-50"
            >
              {salvandoLimiares ? "Salvando..." : "Salvar limiares"}
            </button>
          </div>
        </div>
      </AppShell>
    </AuthGuard>
  );
}
