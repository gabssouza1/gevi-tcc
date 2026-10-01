"use client";

// Suporte — página simples para o usuário reportar bugs, tirar dúvidas ou
// enviar sugestões. Não há backend: o botão "Enviar" monta um mailto: com
// tudo preenchido e abre o cliente de email padrão do usuário. Zero infra
// nova, zero custo, e o filtro natural do "precisa abrir cliente de email"
// já evita spam automático.
import { useEffect, useState } from "react";
import AuthGuard from "@/components/AuthGuard";
import AppShell from "@/components/AppShell";
import { fetchUserAttributes } from "aws-amplify/auth";

// Email de destino do suporte (hardcoded — o objetivo é chegar sempre no
// mesmo endereço, independente do usuário logado).
const EMAIL_SUPORTE = "tccunip68@gmail.com";

type Tipo = "bug" | "duvida" | "sugestao";

const TIPOS: { valor: Tipo; label: string; icon: string; descricao: string }[] = [
  {
    valor: "bug",
    label: "Reportar um problema",
    icon: "bug_report",
    descricao: "Algo não está funcionando como deveria",
  },
  {
    valor: "duvida",
    label: "Tirar uma dúvida",
    icon: "help",
    descricao: "Preciso de ajuda para usar o GEVI",
  },
  {
    valor: "sugestao",
    label: "Enviar uma sugestão",
    icon: "lightbulb",
    descricao: "Ideia para melhorar a plataforma",
  },
];

const INPUT_CLS =
  "w-full bg-surface-container-lowest border border-outline-variant rounded-lg py-sm px-md text-body-lg font-body-lg focus:outline-none focus:ring-2 focus:ring-secondary-container focus:border-transparent text-on-surface";

export default function SuportePage() {
  const [tipo, setTipo] = useState<Tipo>("bug");
  const [assunto, setAssunto] = useState("");
  const [mensagem, setMensagem] = useState("");
  const [erro, setErro] = useState("");
  const [emailUsuario, setEmailUsuario] = useState("");

  // Pega o email do usuário logado só para incluir no corpo do mailto (o
  // destinatário responde para esse email). Não é obrigatório: se falhar
  // (sessão expirada, etc.), o email vai sem essa informação.
  useEffect(() => {
    fetchUserAttributes()
      .then((a) => setEmailUsuario(a.email ?? ""))
      .catch(() => {});
  }, []);

  function enviar() {
    setErro("");
    const asuntoTrim = assunto.trim();
    const mensagemTrim = mensagem.trim();
    if (!asuntoTrim || !mensagemTrim) {
      setErro("Preencha o assunto e a mensagem antes de enviar.");
      return;
    }
    const rotulos: Record<Tipo, string> = {
      bug: "[Bug]",
      duvida: "[Dúvida]",
      sugestao: "[Sugestão]",
    };
    const corpo = [
      `Tipo: ${rotulos[tipo]}`,
      emailUsuario ? `Enviado por: ${emailUsuario}` : "",
      "",
      mensagemTrim,
      "",
      "---",
      "Enviado pela tela de Suporte do GEVI.",
    ]
      .filter(Boolean)
      .join("\n");
    const url = `mailto:${EMAIL_SUPORTE}?subject=${encodeURIComponent(
      `${rotulos[tipo]} ${asuntoTrim}`,
    )}&body=${encodeURIComponent(corpo)}`;
    // Abre o cliente de email padrão do usuário com tudo preenchido.
    window.location.href = url;
  }

  return (
    <AuthGuard>
      <AppShell>
        <div className="mb-xl">
          <h1 className="font-headline-lg text-headline-lg text-primary-container">
            Suporte
          </h1>
          <p className="font-body-lg text-body-lg text-on-surface-variant mt-xs">
            Reporte problemas, tire dúvidas ou envie sugestões. Sua mensagem
            será aberta no seu cliente de e-mail, pronta para envio.
          </p>
        </div>

        <div className="max-w-3xl bg-surface-container-lowest rounded-xl p-lg border border-outline-variant flex flex-col gap-md">
          {/* Seleção do tipo de mensagem (3 cards clicáveis, funciona como
              radio group visual). O tipo entra no assunto do email como
              prefixo, então quem recebe consegue triar rapidamente. */}
          <div>
            <label className="block font-label-caps text-label-caps text-on-surface-variant uppercase mb-sm">
              O que você quer relatar?
            </label>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-sm">
              {TIPOS.map((t) => {
                const ativo = tipo === t.valor;
                return (
                  <button
                    key={t.valor}
                    onClick={() => setTipo(t.valor)}
                    type="button"
                    className={
                      ativo
                        ? "flex flex-col items-start gap-xs p-md rounded-lg border-2 border-secondary-container bg-secondary-container/10 text-left transition-colors"
                        : "flex flex-col items-start gap-xs p-md rounded-lg border-2 border-outline-variant hover:border-secondary-container/50 hover:bg-surface-container text-left transition-colors"
                    }
                  >
                    <span
                      className={`material-symbols-outlined text-2xl ${
                        ativo ? "text-secondary-container" : "text-on-surface-variant"
                      }`}
                    >
                      {t.icon}
                    </span>
                    <span className="font-headline-md text-sm font-semibold text-on-surface">
                      {t.label}
                    </span>
                    <span className="font-body-sm text-xs text-on-surface-variant">
                      {t.descricao}
                    </span>
                  </button>
                );
              })}
            </div>
          </div>

          <div>
            <label className="block font-label-caps text-label-caps text-on-surface-variant uppercase mb-xs">
              Assunto
            </label>
            <input
              className={INPUT_CLS}
              type="text"
              value={assunto}
              onChange={(e) => setAssunto(e.target.value)}
              placeholder="Ex.: Não consigo salvar minha carteira"
              maxLength={120}
            />
          </div>

          <div>
            <label className="block font-label-caps text-label-caps text-on-surface-variant uppercase mb-xs">
              Mensagem
            </label>
            <textarea
              className={`${INPUT_CLS} min-h-[160px] resize-y`}
              value={mensagem}
              onChange={(e) => setMensagem(e.target.value)}
              placeholder="Descreva o problema, dúvida ou sugestão com o máximo de detalhes possível."
              rows={6}
            />
          </div>

          {erro && <p className="text-body-sm text-error">{erro}</p>}

          <div className="flex justify-end">
            <button
              className="bg-primary-container text-on-primary font-headline-md text-headline-md py-sm px-xl rounded-lg hover:bg-on-primary-fixed-variant transition-colors shadow-sm"
              type="button"
              onClick={enviar}
            >
              Enviar
            </button>
          </div>
        </div>
      </AppShell>
    </AuthGuard>
  );
}
