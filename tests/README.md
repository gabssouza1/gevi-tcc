# Testes

Suíte de testes do backend em Python 3.12.

- **Testes unitários**: casos específicos e de borda das Lambdas e do código de `shared/`.
- **Property-based (Hypothesis)**: cobrem as 10 Propriedades de Corretude do design.
- **Mocks AWS (moto)**: simulam DynamoDB e S3 sem acessar a nuvem.

Descoberta de testes configurada no `pyproject.toml` (`testpaths = ["tests"]`).

## Fixtures base (`conftest.py`)

- `dynamodb_simulado` — `resource` boto3 com as 4 tabelas do design já criadas
  (`Users`, `EconomicIndicators`, `Portfolios`, `Historico`) em modo on-demand.
- `s3_simulado` — `client` boto3 com os buckets `s3-investimentos` e
  `audit-bucket`.
- `aws_simulada` — apenas ativa o mock global da AWS (moto 5+) sem recursos.
- `nomes_tabelas` / `nomes_buckets` — mapas nome-lógico → nome-real.
- `_credenciais_aws_falsas` — `autouse`: injeta credenciais falsas em todo teste.

## Marcadores (`pytest -m`)

`unit`, `property`, `aws`, `smoke` — declarados no `pyproject.toml`
(`--strict-markers` ativo). Ex.: `pytest -m aws`.

## Perfis do Hypothesis

`dev` (padrão, 50 exemplos) e `ci` (200 exemplos, sem deadline). Selecione via
`HYPOTHESIS_PROFILE=ci pytest`.

## Executar

```bash
pip install -e ".[dev]"   # ou: pip install -r requirements-dev.txt
pytest
```
