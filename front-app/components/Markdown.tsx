"use client";

// Renderiza markdown (títulos, tabelas, listas, negrito, citações) no visual do
// GEVI. Usado nas respostas do assistente, que vêm em markdown do LLM.
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

const COMPONENTES: Components = {
  h1: ({ children }) => (
    <h1 className="font-headline-md text-headline-md text-primary-container mt-md mb-xs">
      {children}
    </h1>
  ),
  h2: ({ children }) => (
    <h2 className="font-headline-md text-headline-md text-primary-container mt-md mb-xs">
      {children}
    </h2>
  ),
  h3: ({ children }) => (
    <h3 className="font-headline-md text-body-lg font-bold text-on-surface mt-sm mb-xs">
      {children}
    </h3>
  ),
  p: ({ children }) => (
    <p className="font-body-lg text-body-lg my-xs leading-relaxed">{children}</p>
  ),
  ul: ({ children }) => (
    <ul className="list-disc pl-md my-xs space-y-1 font-body-lg text-body-lg">
      {children}
    </ul>
  ),
  ol: ({ children }) => (
    <ol className="list-decimal pl-md my-xs space-y-1 font-body-lg text-body-lg">
      {children}
    </ol>
  ),
  strong: ({ children }) => (
    <strong className="font-bold text-on-surface">{children}</strong>
  ),
  a: ({ children, href }) => (
    <a href={href} className="text-secondary underline" target="_blank" rel="noreferrer">
      {children}
    </a>
  ),
  blockquote: ({ children }) => (
    <blockquote className="border-l-4 border-secondary bg-surface-container pl-sm py-xs my-sm rounded-r-lg text-on-surface-variant">
      {children}
    </blockquote>
  ),
  hr: () => <hr className="border-outline-variant my-sm" />,
  code: ({ children }) => (
    <code className="bg-surface-container px-1 py-0.5 rounded font-mono text-body-sm">
      {children}
    </code>
  ),
  table: ({ children }) => (
    <div className="my-sm overflow-x-auto rounded-lg border border-outline-variant">
      <table className="w-full border-collapse text-left">{children}</table>
    </div>
  ),
  thead: ({ children }) => (
    <thead className="bg-surface-variant">{children}</thead>
  ),
  th: ({ children }) => (
    <th className="p-sm font-headline-md text-body-sm text-on-surface border-b border-outline-variant">
      {children}
    </th>
  ),
  td: ({ children }) => (
    <td className="p-sm font-body-sm text-body-sm border-b border-outline-variant">
      {children}
    </td>
  ),
};

export default function Markdown({ children }: { children: string }) {
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={COMPONENTES}>
      {children}
    </ReactMarkdown>
  );
}
