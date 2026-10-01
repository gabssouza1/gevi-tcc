"use client";

// Minha Conta — dados reais do usuário autenticado (Cognito). Carrega os
// atributos do usuário logado, permite editar nome/telefone/nascimento
// (updateUserAttributes) e trocar a senha (updatePassword). O e-mail é somente
// leitura (alterá-lo exigiria fluxo de verificação).
import { useEffect, useState } from "react";
import Link from "next/link";
import AuthGuard from "@/components/AuthGuard";
import AppShell from "@/components/AppShell";
import {
  fetchUserAttributes,
  updateUserAttributes,
  updatePassword,
} from "aws-amplify/auth";
import { lerPerfil, listarPortfolio, type PerfilInvestidor } from "@/lib/api";

// Rótulos amigáveis (PT) — mesmo mapeamento usado no Dashboard, para os
// termos ficarem consistentes entre telas.
const LABEL_RISCO: Record<string, string> = {
  CONSERVADOR: "Conservador",
  MODERADO: "Moderado",
  ARROJADO: "Arrojado",
};
const LABEL_CONHECIMENTO: Record<string, string> = {
  basico: "Básico",
  avancado: "Avançado",
};

function moedaBR(n: number): string {
  return n.toLocaleString("pt-BR", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

const INPUT_CLS =
  "w-full bg-surface-container-lowest border border-outline-variant rounded-lg py-xs px-sm text-body-sm font-body-sm focus:outline-none focus:ring-2 focus:ring-secondary-container focus:border-transparent text-on-surface";

const CAMPO_CLS =
  "w-full bg-surface-container-lowest border border-outline-variant rounded-lg py-sm pl-xl pr-sm text-body-lg font-body-lg focus:outline-none focus:ring-2 focus:ring-secondary-container focus:border-transparent text-on-surface";

// Valida a data de nascimento (idade entre 18 e 120 anos, sem datas futuras).
// Retorna a mensagem de erro ou null quando válida.
function validarNascimento(nascimento: string): string | null {
  if (!nascimento) return null; // campo opcional aqui; só valida se preenchido
  const nasc = new Date(`${nascimento}T00:00:00`);
  if (Number.isNaN(nasc.getTime())) return "Data de nascimento inválida.";
  const hoje = new Date();
  if (nasc > hoje) return "A data de nascimento não pode ser no futuro.";
  let idade = hoje.getFullYear() - nasc.getFullYear();
  const m = hoje.getMonth() - nasc.getMonth();
  if (m < 0 || (m === 0 && hoje.getDate() < nasc.getDate())) idade -= 1;
  if (idade < 18) return "É necessário ter pelo menos 18 anos.";
  if (idade > 120) return "Verifique a data de nascimento informada.";
  return null;
}

export default function MinhaContaPage() {
  const [nome, setNome] = useState("");
  const [email, setEmail] = useState("");
  const [telefone, setTelefone] = useState("");
  const [nascimento, setNascimento] = useState("");

  const [dadosMsg, setDadosMsg] = useState("");
  const [dadosErro, setDadosErro] = useState("");
  const [salvandoDados, setSalvandoDados] = useState(false);

  const [senhaAtual, setSenhaAtual] = useState("");
  const [novaSenha, setNovaSenha] = useState("");
  const [confirmarSenha, setConfirmarSenha] = useState("");
  const [senhaMsg, setSenhaMsg] = useState("");
  const [senhaErro, setSenhaErro] = useState("");
  const [salvandoSenha, setSalvandoSenha] = useState(false);

  // Resumo (cartão do topo) e perfil do investidor (questionário de suitability).
  const [perfilInvestidor, setPerfilInvestidor] = useState<PerfilInvestidor | null>(
    null,
  );
  const [valorCarteira, setValorCarteira] = useState<number | null>(null);

  // Limites do seletor de data: no máximo 18 anos atrás, no mínimo 120 anos.
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

  useEffect(() => {
    let ativo = true;
    fetchUserAttributes()
      .then((a) => {
        if (!ativo) return;
        setNome(a.name ?? "");
        setEmail(a.email ?? "");
        setTelefone(a.phone_number ?? "");
        setNascimento(a.birthdate ?? "");
      })
      .catch(() => {});
    lerPerfil()
      .then((p) => ativo && setPerfilInvestidor(p))
      .catch(() => {});
    listarPortfolio()
      .then((p) => ativo && setValorCarteira(p.valor_total))
      .catch(() => {});
    return () => {
      ativo = false;
    };
  }, []);

  async function salvarDados() {
    setDadosErro("");
    setDadosMsg("");
    if (!nome.trim()) {
      setDadosErro("Informe seu nome completo.");
      return;
    }
    const erroNasc = validarNascimento(nascimento.trim());
    if (erroNasc) {
      setDadosErro(erroNasc);
      return;
    }
    // Só envia telefone/nascimento em formato aceito pelo Cognito (evita erro).
    const attrs: Record<string, string> = { name: nome.trim() };
    if (/^\+\d{8,15}$/.test(telefone.trim())) attrs.phone_number = telefone.trim();
    if (/^\d{4}-\d{2}-\d{2}$/.test(nascimento)) attrs.birthdate = nascimento;
    setSalvandoDados(true);
    try {
      await updateUserAttributes({ userAttributes: attrs });
      setDadosMsg("Dados atualizados. O nome no topo atualiza ao recarregar.");
    } catch (e) {
      setDadosErro(
        e instanceof Error ? e.message : "Não foi possível salvar os dados.",
      );
    } finally {
      setSalvandoDados(false);
    }
  }

  async function atualizarSenha() {
    setSenhaErro("");
    setSenhaMsg("");
    if (novaSenha.length < 8) {
      setSenhaErro("A nova senha precisa ter ao menos 8 caracteres.");
      return;
    }
    if (novaSenha !== confirmarSenha) {
      setSenhaErro("A confirmação não corresponde à nova senha.");
      return;
    }
    setSalvandoSenha(true);
    try {
      await updatePassword({ oldPassword: senhaAtual, newPassword: novaSenha });
      setSenhaMsg("Senha atualizada com sucesso.");
      setSenhaAtual("");
      setNovaSenha("");
      setConfirmarSenha("");
    } catch (e) {
      setSenhaErro(
        e instanceof Error ? e.message : "Não foi possível atualizar a senha.",
      );
    } finally {
      setSalvandoSenha(false);
    }
  }

  return (
    <AuthGuard>
      <AppShell>
        <div className="mb-xl">
          <h1 className="font-headline-lg text-headline-lg text-primary-container">
            Minha Conta
          </h1>
          <p className="font-body-lg text-body-lg text-on-surface-variant mt-xs">
            Gerencie suas informações pessoais e de segurança.
          </p>
        </div>

        {/* Cartão de resumo: nome + perfil de investidor + valor da carteira,
            de um golpe só — evita ter que abrir Investimentos/Chat só para
            confirmar esses três dados. */}
        <div className="bg-primary-container rounded-xl p-md mb-lg flex flex-col sm:flex-row sm:items-center gap-md sm:gap-xl shadow-sm">
          <div className="flex items-center gap-3">
            <div className="w-14 h-14 rounded-full bg-secondary-container flex items-center justify-center shrink-0">
              <span
                className="material-symbols-outlined text-on-secondary-container text-3xl"
                style={{ fontVariationSettings: "'FILL' 1" }}
              >
                account_circle
              </span>
            </div>
            <div>
              <span className="block font-headline-md text-headline-md font-bold text-on-primary leading-tight">
                {nome || "—"}
              </span>
              <span className="block text-xs text-on-primary-container/80 mt-0.5">
                {email}
              </span>
            </div>
          </div>
          <div className="flex flex-1 flex-wrap gap-md sm:justify-end">
            <div>
              <span className="block font-label-caps text-label-caps text-on-primary-container/80 uppercase">
                Perfil do Investidor
              </span>
              <span className="block font-data-display text-data-display text-on-primary leading-none mt-1">
                {perfilInvestidor?.tolerancia_risco
                  ? LABEL_RISCO[perfilInvestidor.tolerancia_risco] ??
                    perfilInvestidor.tolerancia_risco
                  : "—"}
              </span>
            </div>
            <div>
              <span className="block font-label-caps text-label-caps text-on-primary-container/80 uppercase">
                Valor da Carteira
              </span>
              <span className="block font-data-display text-data-display text-on-primary leading-none mt-1">
                {valorCarteira != null ? `R$ ${moedaBR(valorCarteira)}` : "—"}
              </span>
            </div>
          </div>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-md lg:gap-lg">
          {/* Coluna esquerda: senha */}
          <div className="lg:col-span-1 flex flex-col gap-md lg:gap-lg">
            {/* Alterar Senha */}
            <div className="bg-surface-container-lowest rounded-xl p-md border border-outline-variant">
              <h2 className="font-headline-md text-headline-md text-primary-container mb-md border-b border-surface-variant pb-xs">
                Alterar Senha
              </h2>
              <form className="flex flex-col gap-md" onSubmit={(e) => e.preventDefault()}>
                <div>
                  <label className="block font-label-caps text-label-caps text-on-surface-variant mb-xs">
                    SENHA ATUAL
                  </label>
                  <input
                    className={INPUT_CLS}
                    placeholder="••••••••"
                    type="password"
                    value={senhaAtual}
                    onChange={(e) => setSenhaAtual(e.target.value)}
                  />
                </div>
                <div>
                  <label className="block font-label-caps text-label-caps text-on-surface-variant mb-xs">
                    NOVA SENHA
                  </label>
                  <input
                    className={INPUT_CLS}
                    placeholder="••••••••"
                    type="password"
                    value={novaSenha}
                    onChange={(e) => setNovaSenha(e.target.value)}
                  />
                </div>
                <div>
                  <label className="block font-label-caps text-label-caps text-on-surface-variant mb-xs">
                    CONFIRMAR NOVA SENHA
                  </label>
                  <input
                    className={INPUT_CLS}
                    placeholder="••••••••"
                    type="password"
                    value={confirmarSenha}
                    onChange={(e) => setConfirmarSenha(e.target.value)}
                  />
                </div>
                {senhaErro && <p className="text-body-sm text-error">{senhaErro}</p>}
                {senhaMsg && <p className="text-body-sm text-secondary">{senhaMsg}</p>}
                <button
                  className="mt-sm w-full bg-primary-container text-on-primary font-headline-md text-headline-md py-sm rounded-lg hover:bg-on-primary-fixed-variant transition-colors shadow-sm disabled:opacity-60"
                  type="button"
                  onClick={atualizarSenha}
                  disabled={salvandoSenha}
                >
                  {salvandoSenha ? "Atualizando…" : "Atualizar Senha"}
                </button>
              </form>
            </div>

            {/* Perfil do Investidor: resultado do questionário de suitability
                (avaliar_perfil). Só tolerancia_risco/nivel_conhecimento são
                persistidos — objetivo/horizonte de investimento não ficam
                guardados após o questionário, então não aparecem aqui. */}
            <div className="bg-surface-container-lowest rounded-xl p-md border border-outline-variant">
              <h2 className="font-headline-md text-headline-md text-primary-container mb-md border-b border-surface-variant pb-xs">
                Perfil do Investidor
              </h2>
              <div className="flex flex-col gap-sm">
                <div className="flex justify-between items-center">
                  <span className="font-label-caps text-label-caps text-on-surface-variant">
                    TOLERÂNCIA A RISCO
                  </span>
                  <span className="font-body-lg text-body-lg font-semibold text-on-surface">
                    {perfilInvestidor?.tolerancia_risco
                      ? LABEL_RISCO[perfilInvestidor.tolerancia_risco] ??
                        perfilInvestidor.tolerancia_risco
                      : "—"}
                  </span>
                </div>
                <div className="flex justify-between items-center">
                  <span className="font-label-caps text-label-caps text-on-surface-variant">
                    NÍVEL DE CONHECIMENTO
                  </span>
                  <span className="font-body-lg text-body-lg font-semibold text-on-surface">
                    {perfilInvestidor?.nivel_conhecimento
                      ? LABEL_CONHECIMENTO[perfilInvestidor.nivel_conhecimento] ??
                        perfilInvestidor.nivel_conhecimento
                      : "—"}
                  </span>
                </div>
                <Link
                  href="/onboarding"
                  className="mt-sm w-full text-center bg-surface-container text-primary-container font-headline-md text-headline-md py-sm rounded-lg border border-outline-variant hover:bg-surface-container-high transition-colors"
                >
                  Refazer questionário de perfil
                </Link>
              </div>
            </div>
          </div>

          {/* Coluna direita: dados cadastrais */}
          <div className="lg:col-span-2">
            <div className="bg-surface-container-lowest rounded-xl p-md lg:p-lg border border-outline-variant h-full">
              <h2 className="font-headline-md text-headline-md text-primary-container mb-md border-b border-surface-variant pb-xs">
                Dados Cadastrais
              </h2>
              <form
                className="grid grid-cols-1 md:grid-cols-2 gap-md mt-lg"
                onSubmit={(e) => e.preventDefault()}
              >
                <div className="md:col-span-2">
                  <label className="block font-label-caps text-label-caps text-on-surface-variant mb-xs">
                    NOME COMPLETO
                  </label>
                  <div className="relative">
                    <span className="material-symbols-outlined absolute left-sm top-1/2 -translate-y-1/2 text-on-surface-variant">
                      person
                    </span>
                    <input
                      className={CAMPO_CLS}
                      type="text"
                      value={nome}
                      onChange={(e) => setNome(e.target.value)}
                      placeholder="Seu nome completo"
                    />
                  </div>
                </div>
                <div className="md:col-span-2">
                  <label className="block font-label-caps text-label-caps text-on-surface-variant mb-xs">
                    E-MAIL
                  </label>
                  <div className="relative">
                    <span className="material-symbols-outlined absolute left-sm top-1/2 -translate-y-1/2 text-on-surface-variant">
                      mail
                    </span>
                    <input
                      className={`${CAMPO_CLS} opacity-70 cursor-not-allowed`}
                      type="email"
                      value={email}
                      readOnly
                    />
                  </div>
                </div>
                <div>
                  <label className="block font-label-caps text-label-caps text-on-surface-variant mb-xs">
                    TELEFONE
                  </label>
                  <div className="relative">
                    <span className="material-symbols-outlined absolute left-sm top-1/2 -translate-y-1/2 text-on-surface-variant">
                      phone_iphone
                    </span>
                    <input
                      className={CAMPO_CLS}
                      type="tel"
                      value={telefone}
                      onChange={(e) => setTelefone(e.target.value)}
                      placeholder="+5511987654321"
                    />
                  </div>
                </div>
                <div>
                  <label className="block font-label-caps text-label-caps text-on-surface-variant mb-xs">
                    DATA DE NASCIMENTO
                  </label>
                  <div className="relative">
                    <span className="material-symbols-outlined absolute left-sm top-1/2 -translate-y-1/2 text-on-surface-variant">
                      calendar_today
                    </span>
                    <input
                      className={CAMPO_CLS}
                      type="date"
                      min={minNascISO}
                      max={maxNascISO}
                      value={nascimento}
                      onChange={(e) => setNascimento(e.target.value)}
                    />
                  </div>
                </div>
                <div className="md:col-span-2 mt-lg flex flex-col sm:flex-row sm:items-center sm:justify-end gap-3">
                  <div className="text-body-sm flex-1">
                    {dadosErro && <span className="text-error">{dadosErro}</span>}
                    {dadosMsg && <span className="text-secondary">{dadosMsg}</span>}
                  </div>
                  <button
                    className="bg-primary-container text-on-primary font-headline-md text-headline-md py-sm px-xl rounded-lg hover:bg-on-primary-fixed-variant transition-colors shadow-sm disabled:opacity-60"
                    type="button"
                    onClick={salvarDados}
                    disabled={salvandoDados}
                  >
                    {salvandoDados ? "Salvando…" : "Salvar Alterações"}
                  </button>
                </div>
              </form>
            </div>
          </div>
        </div>
      </AppShell>
    </AuthGuard>
  );
}
