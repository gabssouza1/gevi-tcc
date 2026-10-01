"use client";

import { configurarAmplify } from "@/lib/amplify";

// Configura o Amplify no cliente (executa no import deste módulo client-side).
configurarAmplify();

export default function AmplifyProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  return <>{children}</>;
}
