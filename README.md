# F1 Race Intelligence & Prediction (Gridcast)

[![Python 3.11+](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)
[![CI Tests](https://github.com/AbhiRoy96/f1Predictor/actions/workflows/tests.yml/badge.svg)](./.github/workflows/tests.yml)

A high-utility, point-in-time Formula 1 forecasting system and web platform combining Bayesian and XGBoost driver/constructor strength modeling, Monte Carlo race simulation, rigorous chronological evaluation, and an interactive full-stack workspace called **Gridcast**.

Forecasts produce probabilistic estimates for race wins, podiums, top-10 finishes, retirements (DNF), and final finishing positions.

---

## Key Features

- **Probabilistic Modeling**: Bayesian skill-rating updates alongside XGBoost models for qualifying pace, race pace degradation, and overtaking likelihood.
- **Monte Carlo Race Simulation**: Multi-scenario simulations incorporating safety cars, weather variability, and pit stop strategies.
- **Strict Chronological Evaluation**: Point-in-time data isolation preventing future-data leakage; calibrated probability metrics (Brier score, log-loss, calibration curves).
- **Interactive Full-Stack Platform (Gridcast)**: Modern Next.js UI coupled with a Flask/PostgreSQL backend for historical analysis, predictions, and scenario adjustments.
- **Observability & Telemetry**: Integrated OpenTelemetry instrumentation and ready-to-use Grafana dashboards for monitoring pipeline health and model metrics.

---

## Quick Start

### 1. Python CLI Setup

Requires Python 3.11+ (3.12 recommended) and an active internet connection for initial FastF1 telemetry ingestion.

#### Linux / macOS (Bash / Zsh)
```bash
python3 -m pip install uv
uv venv --python 3.12 .venv
source .venv/bin/activate
uv pip install -r requirements.txt pytest
export PYTHONUTF8=1
python main.py race 2026 "Brazilian Grand Prix"
```

#### Windows (PowerShell)
```powershell
python -m pip install uv
python -m uv venv --python 3.12 .venv
.\.venv\Scripts\Activate.ps1
python -m uv pip install -r requirements.txt pytest
$env:PYTHONUTF8 = "1"
python main.py race 2026 "Brazilian Grand Prix"
```

The first run downloads required historical data via FastF1. Subsequent runs reuse locally cached sessions and serialized model artifacts. See the [CLI Reference](./docs/guide.md#prediction-workflow-and-cli) for fine-grained prediction, training, and evaluation options.

---

### 2. Full-Stack Web Application (Docker Compose)

Launch Gridcast (Next.js), the Flask API, PostgreSQL, and Grafana in containerized isolation:

1. **Configure Environment**:
   ```bash
   cp .env.example .env    # Windows PowerShell: Copy-Item .env.example .env
   ```
   > Set a strong, unique `POSTGRES_PASSWORD` in `.env` before starting containers.

2. **Start Services & Ingest Data**:
   ```bash
   docker compose up --build -d
   docker compose exec api flask --app backend.app:create_app import-data
   ```

3. **Access Services**:
   - **Gridcast Dashboard**: [http://localhost:3000](http://localhost:3000)
   - **Grafana Monitoring**: [http://localhost:3001/d/gridcast-overview](http://localhost:3001/d/gridcast-overview)
   - **Flask API**: [http://localhost:5000](http://localhost:5000)

> [!IMPORTANT]
> This setup is designed for private, local analytical use. Published services bind to `localhost` and Grafana defaults to anonymous admin access. Do not expose without implementing network authentication and access controls.

---

## Repository Layout

```text
backend/             Flask API, PostgreSQL database access, and background workers
web/                 Next.js frontend application (Gridcast)
src/                 Core Python forecasting and modeling engine
  data/              FastF1 ingestion, normalization, and validation routines
  enrichment/        External data integration (Wikipedia/Commons)
  features/          Point-in-time feature engineering & leakage prevention
  models/            Bayesian, XGBoost, calibration, and evaluation algorithms
  observability/     Tracing, metrics, profiling, and runtime monitoring
  simulation/        Monte Carlo race simulation and counterfactual scenarios
  utils/             Configuration management and structured logging
  visualization/     Circuit mapping and telemetry plotting helpers
scripts/             Feature auditing, benchmarking, and web export scripts
tests/               Comprehensive unit, integration, and regression test suites
infra/               Observability configs (Prometheus, Grafana, OpenTelemetry)
docs/                Comprehensive user guides, architecture specs, and archives
data/                Locally cached telemetry and dataset storage (git-ignored)
models/              Serialized trained model artifacts (git-ignored)
reports/             Evaluation benchmarks and audit artifacts
```

---

## Documentation & Reference Index

| Topic | Description & Links |
| :--- | :--- |
| **CLI & Workflows** | [Prediction, Training & Evaluation Commands](./docs/guide.md#prediction-workflow-and-cli) |
| **Data Pipelines** | [Data Provenance](./docs/guide.md#data-collection) and [Pipeline Guide](./src/data/README.md) |
| **Machine Learning** | [Bayesian & XGBoost Model Architecture](./docs/guide.md#models-and-simulation) |
| **Model Validation** | [Probabilistic Calibration & Evaluation](./docs/guide.md#evaluation-and-uncertainty) |
| **Backend Architecture** | [Flask API, PostgreSQL Schema & Worker Tasks](./docs/guide.md#flask-api-and-postgresql) |
| **Frontend Architecture** | [Next.js Web Workspace Reference](./docs/guide.md#nextjs-web-app) |
| **Observability** | [OpenTelemetry Traces & Grafana Dashboards](./docs/guide.md#observability) |
| **Troubleshooting** | [Common Issues & Debugging Procedures](./docs/guide.md#troubleshooting) |
| **Contributions** | [Development Guidelines](./CONTRIBUTING.md) and [Security Policy](./SECURITY.md) |

---

## Verification & Testing

To maximize pipeline reliability and verify analytical correctness, execute the automated test suites:

### Python Backend & Models
```bash
# Run the entire test suite
pytest -q

# Run specific domain test suites
python -m unittest discover -s tests/models
python -m unittest discover -s tests/pipeline
```

### Frontend Type Safety & Build
```bash
cd web
npm run typecheck
npm run build
```
