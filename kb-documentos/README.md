# Documentos do Knowledge Base (RAG)

Esta pasta contém os documentos-semente indexados pelo Amazon Bedrock Knowledge
Base do Sistema Multiagente de Investimentos. O conteúdo é a fonte do RAG
consumido pelo **Agente_Explicador** para adaptar explicações e trazer material
educativo (ver `design.md` — Knowledge Base).

> **Aviso geral:** todo o conteúdo é educativo e de caráter genérico. Não
> constitui recomendação, consultoria ou aconselhamento de investimento. Cada
> documento inclui seu próprio aviso.

## Estrutura

O deploy indexa apenas o prefixo `kb-documentos/` do bucket `s3-investimentos`.
A estrutura de subpastas reflete as categorias do design:

```
kb-documentos/
├── educacao-financeira/     Glossários e tutoriais para iniciantes
│   ├── glossario-financeiro.md
│   └── tutorial-primeiros-passos.md
├── regulamentacao/          CVM, tributação e autorregulação (ANBIMA)
│   ├── cvm-visao-geral.md
│   ├── tributacao-investimentos.md
│   └── anbima-autorregulacao.md
├── metodologias/            Metodologias de análise de risco/retorno
│   ├── value-at-risk.md
│   └── indice-sharpe.md
├── produtos-financeiros/    Classes de ativo suportadas (Tipos_Ativo)
│   ├── renda-fixa-cdb-lci-lca.md   (RENDA_FIXA)
│   ├── tesouro-direto.md           (RENDA_FIXA)
│   ├── fundos-imobiliarios.md      (FII)
│   ├── acoes.md                    (RENDA_VARIAVEL)
│   └── criptoativos.md             (CRIPTO)
└── faq/                     Perguntas frequentes e suporte
    └── faq-geral.md
```

## Como funciona a ingestão

O pipeline de ingestão está no script `scripts/ingerir_kb.py` (na raiz do
projeto). Ele executa duas etapas idempotentes:

1. **Upload** dos arquivos desta pasta para `s3://<bucket>/kb-documentos/`,
   preservando a estrutura de subpastas. Só envia arquivos novos ou alterados
   (compara o MD5 local com o ETag no S3).
2. **Ingestão** via `StartIngestionJob` do Bedrock Knowledge Base, com polling
   do status até `COMPLETE` (ou `FAILED`/`STOPPED`). O job reprocessa apenas o
   que mudou na data source.

O chunking (~512 tokens) e o modelo de embeddings são definidos na infraestrutura
(tarefa 8.2, `infra/stacks/knowledge_base_stack.py`), não no script.

## Como rodar (após o deploy da 8.2)

A ingestão só deve ser executada **depois** do deploy da Knowledge Base (tarefa
8.2), com as variáveis da KB em mãos e credenciais AWS válidas para o profile
`contaA` em `us-east-1`.

Obtenha o `knowledge-base-id` e o `data-source-id` da stack
`MAIA-<ambiente>-KnowledgeBase` (ex.: via console do Bedrock ou saídas do CDK).
O bucket é o `s3-investimentos` com o prefixo do ambiente (ex.:
`dev-s3-investimentos`).

Por argumentos:

```bash
python scripts/ingerir_kb.py \
    --bucket dev-s3-investimentos \
    --knowledge-base-id <KB_ID> \
    --data-source-id <DATA_SOURCE_ID> \
    --region us-east-1 \
    --profile contaA
```

Por variáveis de ambiente:

```bash
export KB_BUCKET=dev-s3-investimentos
export KB_ID=<KB_ID>
export KB_DATA_SOURCE_ID=<DATA_SOURCE_ID>
export AWS_REGION=us-east-1
export AWS_PROFILE=contaA
python scripts/ingerir_kb.py
```

Apenas preparar o bucket (sem ingestão), útil antes da KB existir:

```bash
python scripts/ingerir_kb.py --bucket dev-s3-investimentos --somente-upload
```

## Validar a indexação

Após o job terminar com `COMPLETE`, o script imprime as estatísticas de
indexação (documentos escaneados, indexados, modificados e com falha). Para
validar de forma independente:

- Confira o status da data source e do último job no console do Amazon Bedrock
  (seção Knowledge Bases → Data source → Sync history).
- Faça uma consulta de teste à KB (por exemplo, "o que é o índice de Sharpe?") e
  verifique se o trecho retornado vem destes documentos.

## Manutenção

- Adicione novos documentos na subpasta de categoria adequada.
- Formatos suportados pelo script: `.md`, `.txt`, `.pdf`, `.html`, `.csv`.
- Reexecute `scripts/ingerir_kb.py` para enviar as alterações e reindexar; a
  operação é idempotente e reprocessa apenas o que mudou.
