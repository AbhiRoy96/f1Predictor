---
title: F1 Race Intelligence and Prediction
description: Formula 1 forecasting with a Python pipeline and a Next.js and Flask workspace
---

[![Python 3.11+](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)
[![CI Tests](https://github.com/AbhiRoy96/f1Predictor/actions/workflows/tests.yml/badge.svg)](./.github/workflows/tests.yml)

Point-in-time Formula 1 forecasting with Bayesian and XGBoost strength models,
Monte Carlo simulation, chronological evaluation, and a Next.js / Flask /
PostgreSQL application called Gridcast.

Forecasts estimate win, podium, top-10, retirement, and finishing-position
probabilities. They are probabilistic, not guarantees; use held-out evaluation
before claiming model improvements.

## Quick start

Run commands from the repository root. You need Python 3.11+ (3.12 recommended)
and network access for the initial FastF1 data collection.

```powershell
python -m pip install uv
python -m uv venv --python 3.12 .venv
python -m uv pip install --python .venv\Scripts\python.exe -r requirements.txt pytest
.\.venv\Scripts\Activate.ps1
$env:PYTHONUTF8 = "1"
python main.py race 2026 "Brazilian Grand Prix"
```

The first run downloads selected historical weekends. Later runs reuse saved
sessions, cached data, and matching models. See the
[CLI reference](./docs/guide.md#prediction-workflow-and-cli) for explicit
training, prediction, evaluation, and stage options.

### Run the application

Install Docker with Compose. Copy the example environment file, then set
`POSTGRES_PASSWORD` to a long, random URL-safe value before starting:

```powershell
Copy-Item .env.example .env
# Set POSTGRES_PASSWORD in .env before starting.
docker compose up --build -d
docker compose exec api flask --app backend.app:create_app import-data
```

Open Gridcast at <http://localhost:3000> and Grafana at
<http://localhost:3001/d/gridcast-overview>. Import is explicit and idempotent;
no illustrative data is seeded at startup.

> [!IMPORTANT]
> This is a private, single-user installation without public authentication.
> Published services bind to localhost, and Grafana has anonymous admin access.
> Do not expose it publicly without adding authentication and access controls.

## Find your way around

```text
backend\             Flask API, database access and background worker
web\                 Next.js application
src\                 Shared Python forecasting code
  data\              Collection, normalization and validation code
  enrichment\        Wikipedia and Commons helpers
  features\          Point-in-time feature engineering
  models\            Model implementations, calibration and evaluation
  observability\     Tracing, profiling and runtime metrics
  simulation\        Race simulation and scenarios
  utils\             Shared configuration and logging
  visualization\     Circuit and telemetry helpers
scripts\             Feature audit and web export commands
tests\
  data\              Collection, features, validation and enrichment
  models\            Strength models, evaluation and simulation
  pipeline\          CLI, orchestration and end-to-end regressions
  services\          API, observability and web export contracts
infra\               Observability configuration and dashboards
docs\
  guide.md           Full user and architecture reference
  archive\           Historical recommendations and captured CLI output
data\                Generated datasets and FastF1 cache (not source code)
models\              Generated trained model artifacts (not source code)
reports\             Generated evaluation and audit reports
```

Start with [main.py](./main.py) for the CLI, [backend/app.py](./backend/app.py)
for API routes, and [web/src/app](./web/src/app/) for web pages.
Runtime paths remain defined in [src/utils/config.py](./src/utils/config.py);
Docker commands and application imports are unchanged.

## Documentation

| Topic | Reference |
| ----- | --------- |
| CLI options and workflows | [Prediction workflow](./docs/guide.md#prediction-workflow-and-cli) |
| Collection and feature provenance | [Data collection](./docs/guide.md#data-collection) and [data pipeline reference](./src/data/README.md) |
| Models and simulation | [Model reference](./docs/guide.md#models-and-simulation) |
| Evaluation and uncertainty | [Evaluation reference](./docs/guide.md#evaluation-and-uncertainty) |
| API, database and jobs | [Backend reference](./docs/guide.md#flask-api-and-postgresql) |
| Frontend | [Web application](./docs/guide.md#nextjs-web-app) |
| Telemetry and dashboards | [Observability](./docs/guide.md#observability) |
| Architecture and output files | [Architecture](./docs/guide.md#architecture-and-repository-layout) and [generated artifacts](./docs/guide.md#generated-artifacts) |
| Troubleshooting | [Troubleshooting guide](./docs/guide.md#troubleshooting) |
| Historical research | [v2 recommendations](./docs/archive/v2-recommendations.md), [evaluation output](./docs/archive/evaluation-output.txt) and [variation output](./docs/archive/variation-output.txt) |
| Contributions and security | [Contributing](./CONTRIBUTING.md) and [security policy](./SECURITY.md) |

Archived recommendations and outputs record earlier experiments, not current
behavior or verified accuracy claims.

## Tests

```powershell
$env:PYTHONUTF8 = "1"
python -m unittest discover -s tests
```

Run a single area with `python -m unittest discover -s tests\models`, or use
`python -m pytest -q` for the whole suite.
See [contributing](./CONTRIBUTING.md#3-testing-and-verification) for grouped test
commands and the [full testing reference](./docs/guide.md#tests) for PostgreSQL
integration prerequisites. Frontend checks run from [web](./web/):

```powershell
Set-Location web
npm run typecheck
npm run build
```
