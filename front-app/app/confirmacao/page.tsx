"use client";

// Tela de Confirmação — layout portado do mockup
// sistema/front/confirma_o_de_e_mail_gevi_investimentos/code.html (visual de
// "e-mail enviado"), ESTENDIDO com um formulário de código, pois os mockups não
// cobriam a etapa de código do Cognito (confirmação de cadastro e redefinição
// de senha). Um só ecrã atende os dois fluxos via query param ``flow``:
//   - signup (padrão): código -> confirmSignUp -> /login
//   - reset:           código + nova senha -> confirmResetPassword -> /login
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  confirmSignUp,
  confirmResetPassword,
  resendSignUpCode,
  resetPassword,
} from "aws-amplify/auth";
import Disclaimer from "@/components/Disclaimer";

const LOGO_SRC =
  "https://lh3.googleusercontent.com/aida-public/AB6AXuD52opVJC3uDq3zvg39oTiOcxmuZd5yHgODZUAWfRftvmV56EQSQFiz-3o0__OZ3k5PeyHL9uV0CIkd5Xcw8jj2yF3-k2Zu_AxLBOlb7jakN5Ow1m-u_jduBgawIfd8ZJ7yUJvOOpqQib1uTGwTgdCSF81CW3tF0V_z3yFgEN7ibZ53g62coEvsDAiOU2USJdXcf1u1sTucnEr6Bia3fyDYs4OwX3sIJKxd6dqaPZJ1lCMOe294Eb6YnouLktfkaz2kO6o";

export default function ConfirmacaoPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [flow, setFlow] = useState<"signup" | "reset">("signup");
  const [codigo, setCodigo] = useState("");
  const [novaSenha, setNovaSenha] = useState("");
  const [erro, setErro] = useState<string | null>(null);
  const [carregando, setCarregando] = useState(false);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    setEmail(params.get("email") ?? "");
    setFlow(params.get("flow") === "reset" ? "reset" : "signup");
  }, []);

  async function aoConfirmar(e: React.FormEvent) {
    e.preventDefault();
    setErro(null);
    setCarregando(true);
    try {
      if (flow === "reset") {
        await confirmResetPassword({
          username: email,
          confirmationCode: codigo,
          newPassword: novaSenha,
        });
      } else {
        await confirmSignUp({ username: email, confirmationCode: codigo });
      }
      router.push("/login");
    } catch {
      setErro("Código inválido ou expirado. Verifique e tente novamente.");
    } finally {
      setCarregando(false);
    }
  }

  async function reenviar() {
    setErro(null);
    try {
      if (flow === "reset") {
        await resetPassword({ username: email });
      } else {
        await resendSignUpCode({ username: email });
      }
    } catch {
      setErro("Não foi possível reenviar o código.");
    }
  }

  const titulo =
    flow === "reset" ? "Redefina sua senha" : "Confirme seu e-mail";

  return (
    <div className="bg-background text-on-background font-body-lg min-h-screen flex antialiased">
      <Disclaimer className="fixed bottom-0 inset-x-0 z-50 bg-surface-container-lowest/95 backdrop-blur border-t border-outline-variant py-2 px-md" />
      <div className="flex w-full min-h-screen">
        {/* Left Panel (Desktop only) */}
        <div className="hidden lg:flex lg:w-1/2 bg-[#001a3f] text-on-primary flex-col justify-center items-center p-xl relative overflow-hidden">
          <div className="relative z-10 max-w-lg text-center">
            <h1 className="font-headline-lg text-headline-lg mb-md leading-tight text-on-primary tracking-tight">
              Investimento institucional, agora acessível.
            </h1>
          </div>
        </div>

        {/* Right Panel */}
        <div className="w-full lg:w-1/2 bg-surface-container-lowest flex flex-col justify-center items-center p-lg md:p-xl">
          <div className="w-full max-w-md flex flex-col items-center">
            <img
              alt="GEVI Logo"
              className="h-12 w-auto mb-xl object-contain"
              src={LOGO_SRC}
            />
            <div className="flex flex-col items-center text-center w-full">
              <div className="w-16 h-16 bg-secondary-container rounded-full flex items-center justify-center mb-md">
                <span
                  className="material-symbols-outlined text-on-secondary-container text-4xl"
                  style={{ fontVariationSettings: "'FILL' 1" }}
                >
                  mark_email_read
                </span>
              </div>
              <h2 className="font-headline-md text-headline-md mb-xs text-on-surface">
                {titulo}
              </h2>
              <p className="font-body-lg text-body-lg text-on-surface-variant mb-lg">
                Enviamos um código para {email || "o seu e-mail"}. Verifique sua
                caixa de entrada e a pasta de spam e informe o código abaixo.
              </p>

              <form className="w-full flex flex-col gap-md" onSubmit={aoConfirmar}>
                <div className="w-full text-left">
                  <label
                    className="font-label-caps text-label-caps text-on-surface-variant block mb-xs"
                    htmlFor="codigo"
                  >
                    Código de verificação
                  </label>
                  <input
                    className="w-full py-sm px-md bg-surface-container-lowest border border-outline-variant rounded-lg focus:ring-2 focus:ring-secondary-container focus:border-transparent font-body-lg text-body-lg text-on-surface transition-shadow outline-none tracking-widest text-center"
                    id="codigo"
                    name="codigo"
                    inputMode="numeric"
                    placeholder="______"
                    required
                    value={codigo}
                    onChange={(e) => setCodigo(e.target.value)}
                  />
                </div>

                {flow === "reset" && (
                  <div className="w-full text-left">
                    <label
                      className="font-label-caps text-label-caps text-on-surface-variant block mb-xs"
                      htmlFor="novaSenha"
                    >
                      Nova senha
                    </label>
                    <input
                      className="w-full py-sm px-md bg-surface-container-lowest border border-outline-variant rounded-lg focus:ring-2 focus:ring-secondary-container focus:border-transparent font-body-lg text-body-lg text-on-surface transition-shadow outline-none"
                      id="novaSenha"
                      name="novaSenha"
                      type="password"
                      placeholder="Mín. 8 caracteres, com maiúscula, número e símbolo"
                      required
                      value={novaSenha}
                      onChange={(e) => setNovaSenha(e.target.value)}
                    />
                  </div>
                )}

                {erro && (
                  <p
                    className="font-body-sm text-body-sm text-error text-left"
                    role="alert"
                  >
                    {erro}
                  </p>
                )}

                <button
                  className="w-full bg-primary-container text-on-primary font-headline-md text-body-lg py-sm px-md rounded-lg flex items-center justify-center gap-xs hover:bg-on-primary-fixed-variant transition-colors duration-200 shadow-sm disabled:opacity-60"
                  type="submit"
                  disabled={carregando}
                >
                  {carregando
                    ? "Confirmando..."
                    : flow === "reset"
                      ? "Redefinir senha"
                      : "Confirmar cadastro"}
                </button>
              </form>

              <button
                type="button"
                onClick={reenviar}
                className="mt-lg font-body-sm text-body-sm text-secondary hover:text-primary transition-colors duration-200 font-semibold flex items-center gap-base"
              >
                Não recebeu o código? Tentar novamente
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
