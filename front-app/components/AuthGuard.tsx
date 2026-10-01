"use client";

// Guard de rota autenticada: verifica a sessão do Cognito (getCurrentUser).
// Sem sessão -> redireciona para /login. Enquanto verifica, não renderiza o
// conteúdo protegido (evita flash de dados). Envolve as páginas logadas.
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { getCurrentUser } from "aws-amplify/auth";

export default function AuthGuard({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [estado, setEstado] = useState<"verificando" | "ok">("verificando");

  useEffect(() => {
    let ativo = true;
    getCurrentUser()
      .then(() => ativo && setEstado("ok"))
      .catch(() => router.replace("/login"));
    return () => {
      ativo = false;
    };
  }, [router]);

  if (estado !== "ok") {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background">
        <span className="material-symbols-outlined text-outline animate-spin">
          progress_activity
        </span>
      </div>
    );
  }
  return <>{children}</>;
}
