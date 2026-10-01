"use client";

// Tela de Login — layout portado fielmente do mockup
// sistema/front/login_gevi_investimentos/code.html, com a interatividade
// (toggle de senha) e a integração com o Cognito (signIn via Amplify).
import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { signIn, signOut } from "aws-amplify/auth";
import { perfilExiste } from "@/lib/api";
import Disclaimer from "@/components/Disclaimer";

const LOGO_SRC =
  "https://lh3.googleusercontent.com/aida-public/AB6AXuBlSOIloNFYSYOLOv7_LIWmeoroirGtQtztI91vH55WEi_efA7T0P4ZqAUPrpDWWyheBDgzNkZplAQz5a-PnbTpQ9YleKnvn9sBQ-CQkmvjaCJUZ0jKAy2Qj5uZrQsriLxlwMjZKcGf6DpOrgBt0YakDbSTjC8SSoBFOmFgVmgqYDsXpsgBwmC6K7t-MZqa0GkuEaTid4-Nnw457V8FqjfnOcIkc_-4xroepxUw_b73Pq7bw4zHPAuRrlBUkNARao_9Kdw";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [senha, setSenha] = useState("");
  const [mostrarSenha, setMostrarSenha] = useState(false);
  const [erro, setErro] = useState<string | null>(null);
  const [carregando, setCarregando] = useState(false);

  async function aoEnviar(e: React.FormEvent) {
    e.preventDefault();
    setErro(null);
    setCarregando(true);
    try {
      // Limpa qualquer sessão remanescente (evita UserAlreadyAuthenticatedException
      // quando sobra uma sessão do cadastro/tentativa anterior).
      try {
        await signOut();
      } catch {
        /* sem sessão ativa: segue */
      }
      await signIn({ username: email, password: senha });
      // Sem perfil ainda → questionário de suitability (onboarding). Com perfil
      // → dashboard.
      const tem = await perfilExiste();
      router.push(tem ? "/dashboard" : "/onboarding");
    } catch (err) {
      const nome = (err as { name?: string }).name;
      if (nome === "UserNotConfirmedException") {
        router.push(`/confirmacao?email=${encodeURIComponent(email)}`);
        return;
      }
      if (nome === "UserAlreadyAuthenticatedException") {
        // Já há um usuário logado nesta sessão: roteia conforme o perfil.
        const tem = await perfilExiste();
        router.push(tem ? "/dashboard" : "/onboarding");
        return;
      }
      setErro(
        nome === "NotAuthorizedException"
          ? "E-mail ou senha incorretos."
          : `Não foi possível entrar (${nome ?? "erro"}). Tente novamente.`,
      );
    } finally {
      setCarregando(false);
    }
  }

  return (
    <div className="bg-surface text-on-surface h-screen w-screen overflow-hidden flex flex-col md:flex-row">
      <Disclaimer className="fixed bottom-0 inset-x-0 z-50 bg-surface-container-lowest/95 backdrop-blur border-t border-outline-variant py-2 px-md" />
      {/* Left Side: Image/Branding (Hidden on mobile) */}
      <div className="hidden md:flex flex-1 relative bg-primary-container items-center justify-center overflow-hidden">
        <div className="relative z-10 flex flex-col items-center justify-center p-xl text-center">
          <h2 className="font-headline-lg text-headline-lg text-on-primary mb-sm">
            Inteligência Financeira ao Seu Alcance
          </h2>
          <p className="font-body-lg text-body-lg text-on-primary-container">
            Gerencie seus investimentos com precisão e clareza, utilizando
            ferramentas de nível institucional.
          </p>
        </div>
      </div>

      {/* Right Side: Login Form */}
      <div className="flex-1 flex flex-col justify-center items-center p-sm md:p-xl bg-surface-container-lowest overflow-y-auto">
        <div className="w-full max-w-md">
          {/* Mobile Logo */}
          <div className="md:hidden flex justify-center mb-lg">
            <img alt="GEVI Logo" className="h-12 object-contain" src={LOGO_SRC} />
          </div>

          {/* Header */}
          <div className="flex justify-center mb-lg">
            <img alt="GEVI Logo" className="h-12 object-contain" src={LOGO_SRC} />
          </div>
          <div className="mb-xl text-center md:text-left">
            <h1 className="font-headline-lg-mobile md:font-headline-lg text-headline-lg-mobile md:text-headline-lg text-primary-container mb-xs">
              Acesse sua conta
            </h1>
            <p className="font-body-sm text-body-sm text-on-surface-variant">
              Entre com seus dados para gerenciar seus investimentos.
            </p>
          </div>

          {/* Form */}
          <form className="space-y-sm" onSubmit={aoEnviar}>
            {/* Email Input */}
            <div>
              <label
                className="block font-label-caps text-label-caps text-on-surface-variant mb-xs"
                htmlFor="email"
              >
                E-mail
              </label>
              <div className="relative">
                <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                  <span className="material-symbols-outlined text-outline">mail</span>
                </div>
                <input
                  className="w-full pl-10 pr-3 py-3 border border-outline-variant rounded-lg bg-surface-container-lowest text-on-surface font-body-sm focus:outline-none focus:ring-2 focus:ring-secondary-container focus:border-transparent transition-shadow"
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

            {/* Password Input */}
            <div>
              <label
                className="block font-label-caps text-label-caps text-on-surface-variant mb-xs"
                htmlFor="password"
              >
                Senha
              </label>
              <div className="relative">
                <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                  <span className="material-symbols-outlined text-outline">lock</span>
                </div>
                <input
                  className="w-full pl-10 pr-3 py-3 border border-outline-variant rounded-lg bg-surface-container-lowest text-on-surface font-body-sm focus:outline-none focus:ring-2 focus:ring-secondary-container focus:border-transparent transition-shadow"
                  id="password"
                  name="password"
                  placeholder="••••••••"
                  required
                  type={mostrarSenha ? "text" : "password"}
                  value={senha}
                  onChange={(e) => setSenha(e.target.value)}
                />
                <button
                  className="absolute inset-y-0 right-0 pr-3 flex items-center text-outline hover:text-primary transition-colors"
                  type="button"
                  onClick={() => setMostrarSenha((v) => !v)}
                  aria-label={mostrarSenha ? "Ocultar senha" : "Mostrar senha"}
                >
                  <span className="material-symbols-outlined">
                    {mostrarSenha ? "visibility_off" : "visibility"}
                  </span>
                </button>
              </div>
            </div>

            {erro && (
              <p className="font-body-sm text-body-sm text-error" role="alert">
                {erro}
              </p>
            )}

            {/* Forgot Password */}
            <div className="flex justify-end pt-base">
              <Link
                className="font-body-sm text-body-sm text-secondary hover:text-primary-container transition-colors font-semibold"
                href="/recuperar-senha"
              >
                Esqueci minha senha
              </Link>
            </div>

            {/* Submit Button */}
            <div className="pt-sm">
              <button
                className="w-full flex justify-center items-center py-3 px-4 border border-transparent rounded-lg shadow-sm text-on-primary bg-primary-container hover:bg-on-primary-fixed-variant font-headline-md text-headline-md focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-secondary-container transition-all disabled:opacity-60"
                type="submit"
                disabled={carregando}
              >
                {carregando ? "Entrando..." : "Entrar"}
              </button>
            </div>
          </form>

          {/* Sign Up Link */}
          <div className="mt-lg text-center">
            <p className="font-body-sm text-body-sm text-on-surface-variant">
              Ainda não tem conta?{" "}
              <Link
                className="font-semibold text-secondary hover:text-primary-container transition-colors"
                href="/cadastro"
              >
                Cadastre-se
              </Link>
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
