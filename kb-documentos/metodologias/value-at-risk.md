# Value at Risk (VaR)

> **Aviso:** Conteúdo educativo sobre metodologia de análise de risco. Não é
> recomendação de investimento. Modelos de risco têm limitações e não preveem o
> futuro.

## O que é o VaR

O Value at Risk (VaR), ou "valor em risco", é uma medida estatística que estima
a perda máxima esperada de um ativo ou carteira, em um horizonte de tempo, dado
um nível de confiança. É uma forma de resumir o risco de perda em um único
número.

## Como interpretar

Um exemplo de leitura: "o VaR diário de 95% da carteira é de R$ 1.000". Isso
significa que, em 95% dos dias, espera-se que a perda não ultrapasse R$ 1.000. Em
5% dos dias (os piores cenários), a perda pode ser maior. O VaR não informa o
tamanho dessa perda extrema, apenas o limiar.

## Componentes do cálculo

- **Horizonte de tempo:** período considerado (ex.: 1 dia, 10 dias).
- **Nível de confiança:** probabilidade associada (ex.: 95%, 99%).
- **Volatilidade:** dispersão histórica dos retornos do ativo.

## Métodos comuns

- **Paramétrico (variância-covariância):** assume uma distribuição estatística
  dos retornos (frequentemente a normal) e usa média e desvio-padrão.
- **Histórico:** usa a distribuição real dos retornos passados, sem assumir uma
  forma específica.
- **Monte Carlo:** simula milhares de cenários aleatórios para estimar perdas.

## Limitações

- O VaR não descreve o pior caso possível, apenas um limiar de probabilidade.
- Depende de dados históricos, que podem não refletir eventos futuros.
- Pode subestimar riscos em períodos de crise (eventos de cauda).

Por essas limitações, o VaR costuma ser usado junto de outras métricas de risco.
