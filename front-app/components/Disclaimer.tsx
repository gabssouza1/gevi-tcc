// Aviso global: o GEVI é uma plataforma de apoio à decisão, não executa
// transações. Exibido em todas as páginas (footer do AppShell + telas de auth).
export default function Disclaimer({ className = "" }: { className?: string }) {
  return (
    <div
      className={`flex items-center justify-center gap-2 text-center ${className}`}
      role="note"
    >
      <span className="material-symbols-outlined text-on-surface-variant text-[16px] shrink-0">
        info
      </span>
      <p className="font-body-sm text-body-sm text-on-surface-variant">
        O <strong className="font-semibold">GEVI</strong> é uma plataforma de apoio
        à decisão de investimentos. Não realizamos compra, venda ou qualquer
        transação de ativos.
      </p>
    </div>
  );
}
