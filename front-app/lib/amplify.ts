import { Amplify } from "aws-amplify";

// Configuração do Cognito (contrato exposto pelo backend — ver CfnOutputs da
// SecurityStack). O User Pool ID e o App Client ID são públicos por design em
// SPAs (ficam no bundle do navegador), então podem ser defaults versionados,
// com override por variáveis de ambiente NEXT_PUBLIC_* quando necessário.
const USER_POOL_ID =
  process.env.NEXT_PUBLIC_COGNITO_USER_POOL_ID ?? "POOL_ID";
const USER_POOL_CLIENT_ID =
  process.env.NEXT_PUBLIC_COGNITO_CLIENT_ID ?? "CLIENT_ID";

let configurado = false;

/** Configura o Amplify uma única vez (idempotente) no cliente. */
export function configurarAmplify(): void {
  if (configurado) return;
  Amplify.configure(
    {
      Auth: {
        Cognito: {
          userPoolId: USER_POOL_ID,
          userPoolClientId: USER_POOL_CLIENT_ID,
        },
      },
    },
    { ssr: true },
  );
  configurado = true;
}
