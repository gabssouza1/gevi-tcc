"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

// Root: por ora redireciona para /login (client-side, compatível com export
// estático). Na Fase C a landing page passa a ocupar a raiz.
export default function Home() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/login");
  }, [router]);
  return null;
}
