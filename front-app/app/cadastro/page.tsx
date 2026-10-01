"use client";

// Tela de Cadastro — layout portado do mockup
// sistema/front/cadastro_gevi_investimentos/code.html, com um campo de SENHA
// adicionado (o Cognito exige senha; o mockup não previa) e a integração com o
// Cognito (signUp via Amplify). O perfil de investidor (tolerância a risco e
// nível de conhecimento) NÃO é coletado aqui: é determinado no 1º login pelo
// questionário de suitability em /onboarding (Agente_Perfil).
// Obs.: nascimento e telefone seguem na UI (fidelidade ao mockup), mas não são
// persistidos.
import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { signUp } from "aws-amplify/auth";
import Disclaimer from "@/components/Disclaimer";

const LOGO_SRC =
  "https://lh3.googleusercontent.com/aida-public/AB6AXuC3LBcvxD0brqT94Tex7Hj1_YoQsrsrOZtylw16BUCkNhMtxA1NX_0zss95cmKDmn2nMSo_0B8WSnThIWoasRrVMN3zhHHPTrl9aeeoA1P9V0nIbUi25e30t1hw9GH5imc80L2BBDh2RTH31BRUpTCqkOV9xwuHj7W5v5NPcQb36XeOT3gLoK2dDMP31GilBU3BL7I717I9R6YbvJlK4A0tEWZ8dgQOwT1Sfk-aqoUmGBOrYd0XYe32adL1uwkKX9BA144";

const INPUT_CLS =
  "w-full bg-surface-container-lowest border border-outline-variant rounded-lg py-3 pl-10 pr-4 text-on-surface focus:outline-none focus:ring-2 focus:ring-secondary-container focus:border-transparent font-body-sm text-body-sm transition-all";

// Validação dos dados do cadastro (CT4): nome completo, data de nascimento
// plausível (idade 18–120, sem datas futuras/absurdas) e telefone brasileiro.
function validarCadastro(
  nome: string,
  nascimento: string,
  telefone: string,
): string | null {
  const partes = nome.trim().split(/\s+/).filter(Boolean);
  if (partes.length < 2 || partes.some((p) => p.length < 2)) {
    return "Informe o nome completo (nome e sobrenome).";
  }
  if (/\d/.test(nome)) {
    return "O nome não deve conter números.";
  }
  if (!nascimento) {
    return "Informe a data de nascimento.";
  }
  const nasc = new Date(`${nascimento}T00:00:00`);
  if (Number.isNaN(nasc.getTime())) {
    return "Data de nascimento inválida.";
  }
  const hoje = new Date();
  if (nasc > hoje) {
    return "A data de nascimento não pode ser no futuro.";
  }
  let idade = hoje.getFullYear() - nasc.getFullYear();
  const m = hoje.getMonth() - nasc.getMonth();
  if (m < 0 || (m === 0 && hoje.getDate() < nasc.getDate())) idade -= 1;
  if (idade < 18) return "É necessário ter pelo menos 18 anos.";
  if (idade > 120) return "Verifique a data de nascimento informada.";
  const digitos = telefone.replace(/\D/g, "");
  if (digitos.length < 10 || digitos.length > 11) {
    return "Telefone inválido. Use DDD + número (10 ou 11 dígitos).";
  }
  return null;
}

export default function CadastroPage() {
  const router = useRouter();
  const [nome, setNome] = useState("");
  const [nascimento, setNascimento] = useState("");
  const [telefone, setTelefone] = useState("");
  const [email, setEmail] = useState("");
  const [senha, setSenha] = useState("");
  const [erro, setErro] = useState<string | null>(null);
  const [carregando, setCarregando] = useState(false);
  // Limites do seletor de data: no máximo a data de 18 anos atrás (idade mínima)
  // e no mínimo 120 anos atrás. Assim o próprio campo não deixa escolher quem
  // tem menos de 18 anos nem anos absurdos.
  const _hoje = new Date();
  const maxNascISO = new Date(
    _hoje.getFullYear() - 18,
    _hoje.getMonth(),
    _hoje.getDate(),
  )
    .toISOString()
    .slice(0, 10);
  const minNascISO = new Date(
    _hoje.getFullYear() - 120,
    _hoje.getMonth(),
    _hoje.getDate(),
  )
    .toISOString()
    .slice(0, 10);

  async function aoEnviar(e: React.FormEvent) {
    e.preventDefault();
    setErro(null);
    const problema = validarCadastro(nome, nascimento, telefone);
    if (problema) {
      setErro(problema);
      return;
    }
    setCarregando(true);
    try {
      await signUp({
        username: email,
        password: senha,
        options: { userAttributes: { email, name: nome } },
      });
      router.push(`/confirmacao?email=${encodeURIComponent(email)}`);
    } catch (err) {
      const nome = (err as { name?: string }).name;
      const msg = (err as { message?: string }).message;
      if (nome === "UsernameExistsException") {
        setErro("Este e-mail já está cadastrado.");
      } else if (nome === "InvalidPasswordException") {
        setErro(
          "A senha deve ter 8+ caracteres, com maiúscula, minúscula, número e símbolo.",
        );
      } else {
        setErro(msg ?? "Não foi possível criar a conta. Verifique os dados.");
      }
    } finally {
      setCarregando(false);
    }
  }

  return (
    <div className="bg-background text-on-surface font-body-lg min-h-screen flex antialiased">
      <Disclaimer className="fixed bottom-0 inset-x-0 z-50 bg-surface-container-lowest/95 backdrop-blur border-t border-outline-variant py-2 px-md" />
      {/* Split Screen Container */}
      <div className="flex w-full min-h-screen">
        {/* Left Panel: Brand & Imagery (Hidden on mobile) */}
        <div className="hidden lg:flex w-1/2 bg-primary-container flex-col p-xl relative overflow-hidden justify-center items-center">
          <div
            className="absolute inset-0 opacity-10 pointer-events-none"
            style={{
              backgroundImage:
                "radial-gradient(circle at 100% 0%, #2dbcfe 0%, transparent 60%)",
            }}
          />
          <div className="relative z-10 max-w-[400px] text-center">
            <h1 className="font-headline-lg text-headline-lg text-on-primary mb-6">
              Investimento institucional, agora acessível.
            </h1>
            <p className="font-body-lg text-body-lg text-on-primary-container">
              Junte-se a um grupo exclusivo de investidores e tenha acesso a
              métricas precisas e análises aprofundadas para tomar as melhores
              decisões financeiras.
            </p>
          </div>
          <div className="relative z-10 text-on-primary-container font-body-sm text-body-sm">
            © 2024 GEVI Investimentos. Institucional e Seguro.
          </div>
        </div>

        {/* Right Panel: Registration Form */}
        <div className="w-full lg:w-1/2 flex items-center justify-center p-md md:p-xl bg-surface">
          <div className="w-full max-w-[480px]">
            <div className="flex justify-center mb-8">
              <img alt="GEVI Logo" className="w-64 h-auto" src={LOGO_SRC} />
            </div>
            <div className="mb-lg text-center lg:text-left">
              <h2 className="font-headline-md text-headline-md text-primary mb-xs">
                Crie sua conta
              </h2>
              <p className="font-body-sm text-body-sm text-on-surface-variant">
                Preencha seus dados para iniciar sua jornada de investimentos.
              </p>
            </div>

            <form className="space-y-md" onSubmit={aoEnviar}>
              {/* Nome Completo */}
              <div>
                <label
                  className="block font-label-caps text-label-caps text-on-surface-variant mb-xs"
                  htmlFor="nome"
                >
                  Nome Completo
                </label>
                <div className="relative">
                  <span className="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 text-outline">
                    person
                  </span>
                  <input
                    className={INPUT_CLS}
                    id="nome"
                    name="nome"
                    placeholder="Digite seu nome completo"
                    required
                    type="text"
                    value={nome}
                    onChange={(e) => setNome(e.target.value)}
                  />
                </div>
              </div>

              {/* Data de Nascimento & Telefone Row */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-md">
                <div>
                  <label
                    className="block font-label-caps text-label-caps text-on-surface-variant mb-xs"
                    htmlFor="nascimento"
                  >
                    Data de Nascimento
                  </label>
                  <div className="relative">
                    <span className="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 text-outline">
                      calendar_today
                    </span>
                    <input
                      className={INPUT_CLS}
                      id="nascimento"
                      name="nascimento"
                      required
                      type="date"
                      min={minNascISO}
                      max={maxNascISO}
                      value={nascimento}
                      onChange={(e) => setNascimento(e.target.value)}
                    />
                  </div>
                </div>
                <div>
                  <label
                    className="block font-label-caps text-label-caps text-on-surface-variant mb-xs"
                    htmlFor="telefone"
                  >
                    Telefone
                  </label>
                  <div className="relative">
                    <span className="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 text-outline">
                      phone
                    </span>
                    <input
                      className={INPUT_CLS}
                      id="telefone"
                      name="telefone"
                      placeholder="(00) 00000-0000"
                      required
                      type="tel"
                      value={telefone}
                      onChange={(e) => setTelefone(e.target.value)}
                    />
                  </div>
                </div>
              </div>

              {/* E-mail */}
              <div>
                <label
                  className="block font-label-caps text-label-caps text-on-surface-variant mb-xs"
                  htmlFor="email"
                >
                  E-mail
                </label>
                <div className="relative">
                  <span className="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 text-outline">
                    mail
                  </span>
                  <input
                    className={INPUT_CLS}
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

              {/* Senha (adicionado — exigido pelo Cognito) */}
              <div>
                <label
                  className="block font-label-caps text-label-caps text-on-surface-variant mb-xs"
                  htmlFor="senha"
                >
                  Senha
                </label>
                <div className="relative">
                  <span className="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 text-outline">
                    lock
                  </span>
                  <input
                    className={INPUT_CLS}
                    id="senha"
                    name="senha"
                    placeholder="Mín. 8 caracteres, com maiúscula, número e símbolo"
                    required
                    type="password"
                    value={senha}
                    onChange={(e) => setSenha(e.target.value)}
                  />
                </div>
              </div>

              {erro && (
                <p className="font-body-sm text-body-sm text-error" role="alert">
                  {erro}
                </p>
              )}

              {/* Submit Button */}
              <button
                className="w-full bg-primary-container text-on-primary font-headline-md text-headline-md py-3 rounded-lg hover:bg-surface-tint transition-all focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-primary-container shadow-sm flex items-center justify-center gap-2 mt-lg disabled:opacity-60"
                type="submit"
                disabled={carregando}
              >
                {carregando ? "Criando..." : "Criar Conta"}
                <span className="material-symbols-outlined">arrow_forward</span>
              </button>
            </form>

            {/* Login Link */}
            <div className="mt-xl text-center">
              <p className="font-body-sm text-body-sm text-on-surface-variant">
                Já tenho uma conta?{" "}
                <Link
                  className="font-semibold text-secondary hover:text-secondary-container transition-colors"
                  href="/login"
                >
                  Entrar
                </Link>
              </p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
