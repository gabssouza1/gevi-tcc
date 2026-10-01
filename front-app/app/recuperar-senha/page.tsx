"use client";

// Tela de Recuperar Senha — layout portado fielmente do mockup
// sistema/front/recuperar_senha_gevi_investimentos/code.html, ligado ao Cognito
// (resetPassword via Amplify: envia um código para o e-mail cadastrado).
import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { resetPassword } from "aws-amplify/auth";
import Disclaimer from "@/components/Disclaimer";

const LOGO_SRC =
  "https://lh3.googleusercontent.com/aida-public/AB6AXuA4yv8nzKGr3h_XABuU_i4FS1-Tq_-4i8gX-_MCB3izPmiM5G8f_iCCHDnVslA8aC_6fD9XUjYh3wHbmpg2Kql0EX6CbN28dHC5YqY-WzDnVT943MVcHb3ewxzV5rdcktymre7ptTb--ZJD2U15qwP2sQ6O8WH5totI13vFFOMuVqbSbieWHr7fhDPdsBv9P6gIEx9FZtn_Bqzf4sbMMKmXPVa81uqiI8rVJ7ATDSvKtt9KFuCxdRT6hjTcShiabVMBcyg";

export default function RecuperarSenhaPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [erro, setErro] = useState<string | null>(null);
  const [carregando, setCarregando] = useState(false);

  async function aoEnviar(e: React.FormEvent) {
    e.preventDefault();
    setErro(null);
    setCarregando(true);
    try {
      await resetPassword({ username: email });
      router.push(`/confirmacao?email=${encodeURIComponent(email)}&flow=reset`);
    } catch {
      // Não revela se o e-mail existe (segurança): segue para a tela de sucesso.
      router.push(`/confirmacao?email=${encodeURIComponent(email)}&flow=reset`);
    } finally {
      setCarregando(false);
    }
  }

  return (
    <div className="bg-background text-on-background font-body-lg min-h-screen flex flex-col md:flex-row antialiased">
      <Disclaimer className="fixed bottom-0 inset-x-0 z-50 bg-surface-container-lowest/95 backdrop-blur border-t border-outline-variant py-2 px-md" />
      {/* Left Panel (Desktop only) */}
      <div className="hidden md:flex md:w-[60%] bg-primary-container items-center justify-center p-xl relative overflow-hidden">
        <div className="z-10 text-center max-w-lg">
          <h1 className="font-headline-lg text-headline-lg text-on-primary">
            Investimento institucional,
            <br />
            agora acessível.
          </h1>
        </div>
      </div>

      {/* Right Panel (Form) */}
      <div className="w-full md:w-[40%] bg-surface-container-lowest flex items-center justify-center p-md sm:p-lg md:p-xl min-h-screen">
        <div className="w-full max-w-md flex flex-col items-center">
          <div className="mb-lg w-48">
            <img
              alt="GEVI Logo"
              className="w-full h-auto object-contain"
              src={LOGO_SRC}
            />
          </div>
          <div className="text-center mb-lg w-full">
            <h2 className="font-headline-md text-headline-md text-on-surface mb-xs">
              Recuperar sua senha
            </h2>
            <p className="font-body-sm text-body-sm text-on-surface-variant">
              Insira o e-mail cadastrado para receber as instruções de
              redefinição.
            </p>
          </div>

          <form className="w-full flex flex-col gap-md" onSubmit={aoEnviar}>
            <div className="w-full">
              <label
                className="font-label-caps text-label-caps text-on-surface-variant block mb-xs"
                htmlFor="email"
              >
                E-mail
              </label>
              <div className="relative">
                <div className="absolute inset-y-0 left-0 pl-sm flex items-center pointer-events-none">
                  <span className="material-symbols-outlined text-outline">mail</span>
                </div>
                <input
                  className="w-full pl-xl pr-sm py-sm bg-surface-container-lowest border border-outline-variant rounded-lg focus:ring-2 focus:ring-secondary-container focus:border-secondary-container font-body-lg text-body-lg text-on-surface transition-shadow outline-none"
                  id="email"
                  name="email"
                  placeholder="seu@email.com"
                  required
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                />
              </div>
            </div>

            {erro && (
              <p className="font-body-sm text-body-sm text-error" role="alert">
                {erro}
              </p>
            )}

            <button
              className="w-full bg-primary-container hover:bg-on-primary-fixed-variant text-on-primary font-headline-md text-body-lg py-sm px-md rounded-lg transition-colors flex items-center justify-center gap-2 mt-xs shadow-sm disabled:opacity-60"
              type="submit"
              disabled={carregando}
            >
              {carregando ? "Enviando..." : "Enviar link de recuperação"}
            </button>
          </form>

          <div className="mt-lg">
            <Link
              className="font-body-sm text-body-sm text-secondary hover:text-secondary-container font-semibold transition-colors flex items-center gap-1"
              href="/login"
            >
              Lembrou a senha? Entrar
            </Link>
          </div>
        </div>
      </div>
    </div>
  );
}
