---
title: Gridcast user and architecture guide
description: Forecasting workflows, application architecture, and hosting safeguards.
---

[![Python 3.11+](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](../LICENSE)
[![Flask API](https://img.shields.io/badge/Flask-API-000000.svg)](https://flask.palletsprojects.com/)
[![CI Tests](https://github.com/AbhiRoy96/f1Predictor/actions/workflows/tests.yml/badge.svg)](../.github/workflows/tests.yml)

> **Probabilistic race forecasting, point-in-time features, Bayesian & XGBoost models, and a Next.js / Flask / PostgreSQL workspace for Formula 1.**

Return to the [Project Overview & Navigation](../README.md).  
*Note: All CLI commands are designed to be executed from the repository root.*

---

## Table of Contents

1. [Overview](#overview)
2. [Quick Start](#quick-start)
   - [Prerequisites](#prerequisites)
   - [Environment Setup](#environment-setup)
   - [Generating Forecasts](#generating-forecasts)
   - [Containerized Full-Stack Deployment](#containerized-full-stack-deployment)
3. [Prediction Workflow and CLI](#prediction-workflow-and-cli)
   - [Command Syntax & Actions](#command-syntax--actions)
   - [Prediction Stages](#prediction-stages)
   - [CLI Parameters & Flags](#cli-parameters--flags)
   - [Scenarios & Sensitivity Testing](#scenarios--sensitivity-testing)
4. [Data Collection & Ingestion](#data-collection--ingestion)
   - [Bounded Target Collection](#bounded-target-collection)
   - [Full-Season Batch Collection](#full-season-batch-collection)
5. [Feature Engineering & Leakage Prevention](#feature-engineering--leakage-prevention)
   - [Point-in-Time Guarantees](#point-in-time-guarantees)
   - [Feature Staging & Pipeline Audit](#feature-staging--pipeline-audit)
   - [External News Ingestion](#external-news-ingestion)
6. [Models & Simulation Engine](#models--simulation-engine)
   - [Model Architecture & Variants](#model-architecture--variants)
   - [Ensemble & Scale Fitting](#ensemble--scale-fitting)
   - [Dynamic Bayesian Laplace Challenger](#dynamic-bayesian-laplace-challenger)
   - [Joint Outcome Monte Carlo Simulation](#joint-outcome-monte-carlo-simulation)
7. [Evaluation & Uncertainty Quantification](#evaluation--uncertainty-quantification)
   - [Walk-Forward Validation](#walk-forward-validation)
   - [Single-Event Backtesting](#single-event-backtesting)
   - [Interpreting Uncertainty & Errors](#interpreting-uncertainty--errors)
8. [Flask API & PostgreSQL Backend](#flask-api--postgresql-backend)
   - [Architecture & Job Queue](#architecture--job-queue)
   - [Championship Synchronization](#championship-synchronization)
   - [REST API Reference](#rest-api-reference)
9. [Next.js Web Application (Gridcast)](#nextjs-web-application-gridcast)
10. [Observability & Telemetry](#observability--telemetry)
    - [LGTM Monitoring Stack](#lgtm-monitoring-stack)
    - [Metrics, Traces, Logs & Profiling](#metrics-traces-logs--profiling)
    - [Compute Cost & GPU Monitoring](#compute-cost--gpu-monitoring)
11. [Architecture & Repository Layout](#architecture--repository-layout)
12. [Generated Artifacts & File Schemas](#generated-artifacts--file-schemas)
13. [Testing & Quality Assurance](#testing--quality-assurance)
14. [Troubleshooting & Diagnostics](#troubleshooting--diagnostics)
15. [Roadmap & Future Milestones](#roadmap--future-milestones)
16. [Data Sources & Attribution](#data-sources--attribution)

---

## Overview

F1 Race Intelligence estimates driver win, podium, top-10, retirement (DNF), and final finishing position probabilities across Grand Prix, sprint, and qualifying sessions. The system integrates FastF1 session telemetry, point-in-time feature engineering, competitive strength models, Monte Carlo race simulation, and a full-stack Next.js web application backed by a containerized Flask API, an asynchronous job worker, and a PostgreSQL database.

The CLI defaults to an ensemble blending dynamic Bayesian Plackett-Luce strength with XGBoost finisher ranking. Standalone Bayesian and legacy ranking models remain available for comparative evaluation. While initial validation focuses on the 2026 Brazilian Grand Prix at Interlagos, the pipeline generalises across all scheduled modern Formula 1 events.

```
       [ FastF1 & External Telemetry ]
                      │
                      ▼
        [ Point-in-Time Features ]
                      │
         ┌────────────┴────────────┐
         ▼                         ▼
 [ Dynamic Bayesian ]      [ XGBoost Ranker ]
         └────────────┬────────────┘
                      ▼
           [ Ensemble Calibration ]
                      │
                      ▼
        [ Monte Carlo Simulation ]
                      │
         ┌────────────┴────────────┐
         ▼                         ▼
 [ Probabilistic Reports ]   [ PostgreSQL & Gridcast UI ]
```

> [!IMPORTANT]
> **Operational Scope & Security**: This repository provides a private, single-user analytical installation. Docker Compose binds Next.js, Grafana, and OpenTelemetry to `localhost`, keeping PostgreSQL and Flask internal. Grafana includes local anonymous admin access. Do not expose this service to untrusted networks without adding robust authentication, TLS termination, and ingress access controls.

---

## Quick Start

### Prerequisites

* **Python**: 3.11 or 3.12 (Python 3.12 is standardized in CI).
* **Package Manager**: `uv` or `pip`.
* **Network**: Active internet access for FastF1 and Jolpica API data ingestion.
* **Containers (Optional)**: Docker Engine 24+ and Docker Compose v2 for full-stack deployment.

---

### Environment Setup

#### Linux / macOS (Bash / Zsh)
```bash
python3 -m pip install uv
uv venv --python 3.12 .venv
source .venv/bin/activate
uv pip install -r requirements.txt pytest
export PYTHONUTF8=1
```

#### Windows (PowerShell)
```powershell
python -m pip install uv
python -m uv venv --python 3.12 .venv
.\.venv\Scripts\Activate.ps1
python -m uv pip install -r requirements.txt pytest
$env:PYTHONUTF8 = "1"
```

---

### Generating Forecasts

Execute automated collection, feature generation, model training/discovery, and race simulation with a single command:

```bash
python main.py race 2026 "Brazilian Grand Prix"
```

*Note: Initial runs ingest historical event data from 2021 through the target year. Downloaded sessions are cached locally in `data/raw/` and `data/cache/` for accelerated subsequent runs.*

#### Explicit Staged Training & Inference
```bash
# 1. Train and serialize model artifact at the post-qualifying stage
python main.py train race 2026 "Brazilian Grand Prix" --stage post_quali

# 2. Perform probabilistic prediction without re-collecting or retraining
python main.py predict race 2026 "Brazilian Grand Prix" --stage post_quali --skip-collection
```

---

### Containerized Full-Stack Deployment

Launch the Gridcast UI, Flask API, PostgreSQL database, and Grafana monitoring stack via Docker Compose:

```bash
# 1. Configure environment variables
cp .env.example .env    # Windows: Copy-Item .env.example .env

# 2. Build and launch services in background
docker compose up --build -d

# 3. Initialize database schema and import existing model artifacts
docker compose exec api flask --app backend.app:create_app db-init
docker compose exec api flask --app backend.app:create_app import-data
```

#### Service Endpoints
* **Gridcast Web Workspace**: [http://localhost:3000](http://localhost:3000)
* **Grafana Telemetry Dashboard**: [http://localhost:3001/d/gridcast-overview](http://localhost:3001/d/gridcast-overview)
* **Flask Core API**: [http://localhost:5000/api/health](http://localhost:5000/api/health)

---

## Prediction Workflow and CLI

### Command Syntax & Actions

Run all commands from the repository root:

```text
python main.py [<action>] <session> <year> "<event>" [options]
```

* **`<session>`**: `race`, `sprint`, or `qualifying`.
* **`<year>`**: 2021 through current season.
* **`"<event>"`**: Event title, host city, or country (case-insensitive fuzzy matching; e.g., `"São Paulo"`, `"Brazilian Grand Prix"`, `"Interlagos"`).

#### Available Actions
* *(Omitted)*: Automatically collects missing data, generates features, reuses or fits a model, and writes probabilistic predictions.
* **`train`**: Fits and serializes a versioned model artifact without running post-fit predictions.
* **`predict`**: Loads an existing matching model artifact and produces a forecast (fails if artifact is absent or stale).
* **`evaluate`**: Executes chronological walk-forward validation and produces comprehensive calibration and Brier score reports.

---

### Prediction Stages

Prediction stages enforce strict temporal filtering to prevent future-data contamination:

| Stage | Allowed In-Weekend Evidence | Typical Use Case |
| :--- | :--- | :--- |
| `pre_weekend` | Historical data strictly prior to the first on-track session of the weekend. | Early pre-event forecasts. |
| `post_fp` | Ingests Practice 1 / Practice 2 sessions prior to any competitive session cutoff. | Friday analysis after initial practice pace runs. |
| `post_quali` *(Default)* | Ingests completed qualifying order, grid positions, and preceding sprint sessions. | Final pre-race forecast with confirmed starting grid. |

> [!NOTE]
> For qualifying forecasts, the target qualifying session is always held out regardless of stage. On sprint weekends, `post_fp` excludes practice sessions that take place after sprint qualifying.

---

### CLI Parameters & Flags

| Option | Default | Purpose & Description |
| :--- | :--- | :--- |
| `--stage` | `post_quali` | Temporal observation stage (`pre_weekend`, `post_fp`, `post_quali`). |
| `--strength-model` | `ensemble` | Modeling algorithm: `ensemble`, `bayesian`, or `legacy`. |
| `--skip-collection` | `False` | Bypasses network calls and builds strictly from local raw Parquet files. |
| `--model-path` | Auto | Overrides default model resolution with a specific path. |
| `--n-simulations` | `20000` | Number of Monte Carlo race simulations (higher values reduce MCSE). |
| `--seed` | `42` | Random seed ensuring deterministic, reproducible simulations. |
| `--scenario` | `standard_dry` | Counterfactual sensitivity scenario. |
| `--events-per-season` | `6` | Detailed historical-weekend budget; recent results-only coverage is collected separately. |
| `--similar-circuits` | Built-in | Custom list of circuit names for historical data inclusion. |
| `--measure-variation` | `False` | Backtests completed target event, generating predicted-vs-actual residuals. |

---

### Scenarios & Sensitivity Testing

Evaluate team and driver sensitivity under simulated adverse track and strategic conditions:

```bash
python main.py predict race 2026 "Brazilian Grand Prix" --scenario wet_chaos --skip-collection
```

* **`standard_dry`**: Baseline probabilistic execution reflecting nominal dry conditions.
* **`wet_chaos`**: Amplified weather volatility, elevated collision probabilities, and increased pit variance.
* **`high_safety_car`**: Increased safety car deployment probability, compressing field gaps.
* **`high_degradation`**: Elevated tyre degradation penalties favoring superior tyre-management packages.
* **`reliability_attrition`**: Elevated mechanical failure rates testing constructor durability.

---

## Data Collection & Ingestion

### Bounded Target Collection

The CLI collects complete elapsed results-only coverage for the target and previous
season without downloading laps for every event. Detailed session collection for
older seasons remains bounded (default: six relevant weekends per season from 2021).
Event selection prioritization for that detailed collection:

1. **Target Event**: Exact match by name or normalized circuit key.
2. **Preceding Events**: Immediate chronological predecessors in the championship calendar.
3. **Similar Circuit Profiles**: High-correlation circuit layouts (e.g., Interlagos links with Austin, Spielberg, Barcelona, Silverstone, Suzuka).
4. **Additional Calendar Rounds**: Preceding rounds up to the allocated budget.

```bash
# Custom budget and circuit similarity overrides
python main.py race 2026 "Brazilian Grand Prix" --events-per-season 8 --similar-circuits Austin Silverstone Spa
```

---

### Full-Season Batch Collection

For comprehensive offline research, ingest full championship seasons spanning 2018 through 2026:

```bash
# Ingest entire historical range
python -m src.data.collect_season --start-year 2021 --end-year 2026

# Ingest specific selected seasons
python -m src.data.collect_season --year 2022 --year 2023 --year 2024
```

Collected sessions are serialized to `data/raw/<year>/` in Parquet format, partitioned by session type, preserving lap-by-lap telemetry, weather streams, and race control timestamps.

---

## Feature Engineering & Leakage Prevention

### Point-in-Time Guarantees

The feature pipeline enforces strict temporal separation to guarantee zero data leakage:

* **Strict Temporal Filtering**: A feature at time $t$ uses only data where session start time $< t$.
* **Held-Out Target Labels**: Target classification outcomes (`target_position`, `target_dnf`) are stored in isolated target columns and never used as model inputs.
* **Grid Publication Invariant**: Official starting grids are utilized only if `GridPublishedAt` precedes the stage cutoff; otherwise, qualifying classification proxies are used with explicit `grid_is_quali_proxy=1` indicators.
* **Practice Filtering**: Practice pace features exclude invalidated, in-lap, out-lap, and yellow/red-flagged laps.

---

### Feature Staging & Pipeline Audit

Generate canonical staged feature tables (`race_post_quali_features.parquet`, etc.):

```bash
python -m src.features.engineer_features
```

Audit feature integrity, verify missingness flags, and check grid distributions:

```bash
python -m scripts.audit_features --features data/processed/race_features.parquet
```

The audit outputs `reports/feature_audit.json` and verifies that no collinear proxies, missing columns, or degenerate grids contaminate the dataset.

---

### External News Ingestion

Optional contextual news can be supplied via `data/raw/news.csv` containing `PublishedAt` (UTC ISO timestamp) and `EventName` or `CircuitName`. News items are matched and ingested strictly if published prior to the session cutoff.

---

## Models & Simulation Engine

### Model Architecture & Variants

1. **`ensemble` (Production Default)**: Blends latent Bayesian Plackett-Luce driver/constructor ratings with conditional-finisher XGBoost ranking models.
2. **`bayesian`**: Dynamic Plackett-Luce state-space model with Laplace approximation and Gaussian shrinkage.
3. **`legacy`**: Gradient-boosted tree and logistic regression ranking model with recency-weighted decay.

---

### Ensemble & Scale Fitting

For normalized Bayesian latent strength $b$ and XGBoost strength $x$, the combined rating $s$ is:

$$s = w \cdot b + (1 - w) \cdot x$$

* **Out-of-Fold (OOF) Calibration**: Training splits evaluate past-only cross-validation folds. The conditional top-10 Plackett-Luce ranking objective calibrates ensemble weights and temperature using finishers, while retirement risk is modeled separately.
* **Temperature Scaling**: Calibrates final probability sharpness against historical empirical frequencies using an expanding historical window.

---

### Dynamic Bayesian Laplace Challenger

* **State Space Dynamics**: Latent driver skill and constructor performance evolve as Gaussian random walks over time ($\sigma_{\text{driver}} = 0.035$, $\sigma_{\text{constructor}} = 0.10$ per 14-day interval).
* **Regulation Boundaries**: Major technical regulation shifts (e.g., 2022, 2026) apply shrinkage, retaining 25% of prior constructor mean and resetting variance.
* **Teammate Covariance**: Shared constructor latent states naturally couple teammate performances, capturing team-level operational variance.

---

### Joint Outcome Monte Carlo Simulation

For each simulated race iteration ($N = 20,000$ iterations by default):

1. **Sample Latent Strengths**: Correlated driver/constructor strengths are sampled from the posterior covariance matrix.
2. **Sample Attrition (DNF)**: Mechanical failures and collision probabilities are drawn via a shrunk Gaussian copula preserving marginal retirement risks.
3. **Simulate Finishing Order**: Active finishers are ordered probabilistically via Plackett-Luce permutation sampling; retirees are appended in reverse order of retirement hazard.
4. **Aggregate Probability Metrics**: Computes empirical probability distributions for win ($P1$), podium ($P1\text{--}P3$), points ($P1\text{--}P10$), and exact finishing rank quantiles ($P10, P50, P90$).

---

## Evaluation & Uncertainty Quantification

### Walk-Forward Validation

Evaluate forecasting efficacy using chronological expanding-window backtesting:

```bash
# Comprehensive walk-forward evaluation
python main.py evaluate race 2026 "Brazilian Grand Prix" --stage post_quali

# Compare standalone Bayesian vs Legacy architectures
python main.py evaluate race 2026 "Brazilian Grand Prix" --strength-model bayesian --skip-collection
python main.py evaluate race 2026 "Brazilian Grand Prix" --strength-model legacy --skip-collection
```

#### Evaluation Metrics Reported
* **Brier Scores**: Calibration and accuracy across Win, Podium, Top-10, and DNF predictions.
* **DNF Brier Skill Score**: Skill improvement over constant historical baseline:
  $$\text{Skill} = 1 - \frac{\text{Brier}_{\text{model}}}{\text{Brier}_{\text{baseline}}}$$
* **Log-Loss & Ranked Probability Score (RPS)**: Multi-category ordinal error distribution metrics.
* **Rank Correlation (Kendall's $\tau$, Spearman's $\rho$)**: Correlation between predicted rank and actual finish.
* **Confidence Intervals**: 500-iteration circular block-bootstrapped error ranges.

---

### Locked model-readiness experiments

Feature recency and form use calendar-time windows rather than treating every
collected race as equally spaced. Ordinary evaluation now records paired
model-minus-grid and model-minus-past-selected-best-baseline intervals in the
confidence report. The Model Lab displays these intervals without claiming
an improvement when the interval crosses zero or evidence is missing.

Choose a genuine cutoff and validation start before tuning. The following
PowerShell workflow locks feature data, code and runtime versions, compares
12/18/24/36-month and all-history windows on common validation races, and
reserves the final test for one execution:

```powershell
$py = ".venv\Scripts\python.exe"
& $py -m scripts.model_readiness lock --features data\processed\race_post_quali_features.parquet --lock reports\readiness-lock.json --cutoff $cutoff --validation-start $validationStart
& $py -m scripts.model_readiness ablate --lock reports\readiness-lock.json --output reports\window-ablation
& $py -m scripts.model_readiness final-test --lock reports\readiness-lock.json --output reports\final-test
```

Set `$cutoff` and `$validationStart` to your selected timezone-aware timestamps.
Do not treat an ordinary exploratory `evaluate` run as the locked final test.
A failed final-test execution still consumes the reservation; investigate it
without silently reusing the held-out sample for tuning.

Model artifacts record exact Python and package versions. Loading an artifact
with missing or incompatible metadata raises an explicit retraining error.
Joblib files remain trusted-code artifacts; never load user-supplied models.

### Scheduled and prospective forecasts

Scheduled forecasts submit to the existing protected queue, not a second disk-writing
worker. Set `BACKEND_URL` to the internal API and obtain `PROXY_AUTH_TOKEN` from
Key Vault. The following commands require actual observed publication times:

```powershell
& $py -m scripts.scheduled_forecast --year $year --event $event --phase post_quali --qualifying-completed-at $qCompleted --race-start $raceStart
& $py -m scripts.scheduled_forecast --year $year --event $event --phase official_grid --qualifying-completed-at $qCompleted --race-start $raceStart --grid-published-at $gridPublished
```

Both phases use `stage=post_quali`, but persist distinct `forecast_phase` and
`source_published_at_utc` values. Queue admission validates the phase and timezone;
the pipeline also checks publication and forecast timing against the target start.
The operator must supply genuine qualifying/grid publication observations;
cron scheduling alone does not discover official penalties.
The official-grid phase requires explicit eligible grid positions and publication
provenance for every entrant. Missing/imputed positions or qualifying proxies
are rejected before training rather than mislabeled as post-penalty evidence.

Pre-race forecasts automatically create immutable archive files. Score these only
after completed classifications become available:

```powershell
$forecasts = (Get-ChildItem data\predictions\archive\*.parquet).FullName
& $py -m scripts.live_scoreboard --forecasts $forecasts --results data\raw\2026_r_results.parquet --output reports\live-scoreboard.csv
```

These commands have not been run against live data as part of the repository
implementation. A real collection, window ablation, locked test, evaluation
reference and prospective scoreboard remain launch requirements.

### Single-Event Backtesting

Generate predicted-vs-actual residuals on a completed historical event without modifying serialized production models:

```bash
python main.py race 2024 "São Paulo Grand Prix" --measure-variation
```

Results are saved to `data/predictions/variation_race_2024_rnd21_ensemble_post_quali.parquet`.

---

### Interpreting Uncertainty & Errors

* **`p10_finish` & `p90_finish`**: 10th and 90th percentile predicted finishing positions conditional on finishing the race.
* **`win_probability_mcse`**: Monte Carlo Standard Error estimating numerical sampling noise ($\text{MCSE} \approx \sqrt{\frac{p(1-p)}{N}}$). Increasing `--n-simulations` reduces MCSE.

---

## Flask API & PostgreSQL Backend

### Architecture & Job Queue

The backend (`backend/`) manages database persistence, API routing, and asynchronous background forecasting jobs:

```
[ HTTP Request ] ──► [ Flask API ] ──► [ PostgreSQL DB (Job Queue) ]
                                                    ▲
                                                    │ Advisory Lock
[ Parquet / Models ] ◄───────────────────── [ Worker Process ]
```

* **Advisory Lock Concurrency**: A PostgreSQL advisory lock ensures a single worker process handles disk-bound model and Parquet updates, preventing file corruption.
* **Idempotency**: Data imports and season syncs can be executed repeatedly without generating duplicate records.

### Hosted queue safeguards

The web proxy and API accept at most 16 KiB per JSON request. Hosted API requests
require the shared `PROXY_AUTH_TOKEN` and a pseudonymous user supplied by the
authenticated web proxy. Set `HOSTED_MODE=true` on both applications and keep the
API ingress internal. Header checks alone are not authentication: ACA must enforce
Microsoft Entra sign-in and replace client principal headers before web ingress.

The queue admits at most three active jobs per user and ten globally. Predictions
are limited to 50,000 simulations and twelve detailed weekends per older season.
Duplicate active forecasts reuse the existing job for the same user; another user's
active duplicate is rejected. Successful identical requests are reused for one hour.
The identity includes year, event, session, stage, strength model and seed.
Changing only simulation count or collection options does not create an active duplicate.

Season sync is single-flight across the queue, with a one-hour interval per season.
Upstream requests are spaced by one second and retry transient failures with
exponential backoff and `Retry-After`. Database-backed write throttles permit
twenty requests per minute per user and client-address pseudonym across API replicas.
Job listings expose only the current user's jobs; forecasts and settings are shared.

The worker holds its session advisory lock on a direct PostgreSQL connection on
port 5432. Each production job runs in a bounded subprocess with a heartbeat;
`JOB_MAX_RUNTIME_SECONDS` defaults to 3600 and `JOB_HEARTBEAT_SECONDS` to 15.
On Linux, timeout or loss of the worker lock terminates the entire job process group.
Interrupted jobs fail visibly and are not silently retried.

Run schema v3 migrations only through `db-init`, before deploying API and worker
images. Existing jobs without an owner remain administrative records and are not
shown in user job lists. `pg_stat_statements` is optional: configure it on the server
and set `ENABLE_PG_STAT_STATEMENTS=true` on the init job if database profiling is needed.
Application startup never applies migrations.

See the [ACA hosting checklist](patch/f1_predictor_aca_hosting_checklist.md)
for infrastructure entry points and the cloud-only launch checks.

---

### Championship Synchronization

Synchronize official championship standings and historical session classifications from Jolpica:

```bash
# Local same-origin proxy; hosted use requires your signed-in browser
curl -X POST http://localhost:3000/api/seasons/2024/sync -H "Content-Type: application/json" -d "{}"
```

---

### REST API Reference

| Method | Endpoint | Description | Payload / Parameters |
| :--- | :--- | :--- | :--- |
| `GET` | `/api/health` | Healthcheck and PostgreSQL schema status. | None |
| `GET` | `/api/runs` | List generated forecast runs. | `?limit=20` |
| `GET` | `/api/runs/<id>` | Retrieve detailed forecast payload by run ID. | None |
| `POST` | `/api/predictions` | Queue a new forecasting job. | `{"year": 2026, "event": "Brazilian Grand Prix", "stage": "post_quali"}` |
| `GET` | `/api/jobs` | Monitor active and completed background jobs. | None |
| `GET` | `/api/jobs/<id>` | Inspect specific job status and error logs. | None |
| `GET` | `/api/settings` | Retrieve default prediction settings. | None |
| `PUT` | `/api/settings` | Update global prediction defaults. | `{"n_simulations": 30000, "events_per_season": 8}` |
| `GET` | `/api/seasons/<year>` | Fetch synced season calendar and standings. | None |
| `POST` | `/api/seasons/<year>/sync` | Trigger asynchronous season resync. | `{}` |

---

## Next.js Web Application (Gridcast)

The **Gridcast** frontend (`web/`) is built with Next.js (App Router), TypeScript, and Tailwind CSS, featuring an F1 TV-inspired high-contrast analytical interface.

```bash
# Local frontend development
cd web
npm install
npm run dev
```

### Application Routes
* **`/` (Home)**: System overview, platform architecture, and operational status.
* **`/predictions`**: Interactive probability matrices, driver head-to-head comparisons, position heatmaps, and counterfactual scenario selectors.
* **`/seasons`**: Official championship calendars, session classifications, and driver/constructor standings.
* **`/model-lab`**: Interactive walk-forward evaluation dashboards, Brier score leaderboards, and reliability diagrams.
* **`/settings`**: Configure default simulation parameters, collection budgets, and active model variants.
* **`/privacy`**: Stored data, pseudonymous admission identities and retention responsibilities.
* **`/health`**: Lightweight web probe. API readiness separately checks PostgreSQL.

---

## Observability & Telemetry

### LGTM Monitoring Stack

The platform includes an OpenTelemetry-native monitoring stack defined in `compose.yaml` using `grafana/otel-lgtm:0.40.0`:

```
[ Python Worker / Flask / Next.js ]
                │
                ▼ (OTLP gRPC/HTTP :4317/:4318)
    [ OpenTelemetry Collector ]
        ┌───────┼───────┬───────┐
        ▼       ▼       ▼       ▼
    [ Loki ] [ Tempo ] [ Prom ] [ Pyroscope ]
        └───────┼───────┴───────┘
                ▼
      [ Grafana Dashboards ]
```

---

### Metrics, Traces, Logs & Profiling

* **Metrics (Prometheus)**: Ingests model fit duration, Monte Carlo simulation rates, queue wait times, and process memory utilization.
* **Traces (Tempo)**: Tracks end-to-end trace propagation across Next.js UI requests, Flask API endpoints, database operations, and model inference steps.
* **Logs (Loki)**: Correlates structured JSON logs directly with active Trace IDs and Span IDs.
* **Continuous Profiling (Pyroscope)**: Provides Linux on-CPU profiling for model fitting and simulation routines.

---

### Compute Cost & GPU Monitoring

Environment variables for observability configuration (`.env`):

| Variable | Default | Purpose |
| :--- | :--- | :--- |
| `OTEL_ENABLED` | `true` | Enables OpenTelemetry metric and trace export. |
| `OTEL_TRACE_SAMPLE_RATIO` | `1.0` | Trace sampling ratio (0.0 to 1.0). |
| `PYROSCOPE_ENABLED` | `true` | Enables continuous CPU profiling in Linux containers. |
| `ML_COMPUTE_HOURLY_USD` | *Unset* | Optional hourly compute cost rate (USD) for cost tracking. |

#### Optional NVIDIA GPU Telemetry
For environments equipped with NVIDIA GPUs, enable NVML hardware metrics:
```bash
docker compose -f compose.yaml -f compose.gpu.yaml up --build -d
```

---

## Architecture & Repository Layout

```
f1Predictor/
├── backend/                  # Flask REST API, PostgreSQL schema & job worker
├── web/                      # Next.js App Router frontend application
├── src/                      # Core forecasting engine
│   ├── data/                 # FastF1 data ingestion, normalization & validation
│   ├── enrichment/           # Wikipedia and contextual media metadata helpers
│   ├── features/             # Point-in-time feature engineering & leakage prevention
│   ├── models/               # Bayesian Laplace, XGBoost, calibration & baselines
│   ├── observability/        # OpenTelemetry instrumentation, metrics & profiling
│   ├── simulation/           # Monte Carlo race simulation & sensitivity scenarios
│   ├── utils/                # Configuration, logging, and runtime paths
│   └── visualization/        # Circuit mapping & telemetry plotting utilities
├── scripts/                  # Feature auditing & batch export scripts
├── infra/                    # LGTM Grafana dashboards & OpenTelemetry configurations
├── tests/                    # Pytest & Unittest validation suites
├── docs/                     # Technical documentation & architectural guides
├── data/                     # Local data cache & Parquet files (git-ignored)
├── models/                   # Serialized model artifacts (git-ignored)
└── reports/                  # Evaluation benchmarks & audit reports (git-ignored)
```

---

## Generated Artifacts & File Schemas

| Directory | File Pattern | Content & Format |
| :--- | :--- | :--- |
| `data/raw/` | `<year>/<session>_results.parquet` | Raw lap telemetry, timing, weather, and race control events. |
| `data/processed/` | `<session>_<stage>_features.parquet` | Point-in-time engineered feature tables with SHA256 sidecars. |
| `data/predictions/` | `predictions_<session>_<year>_rnd<rnd>_<variant>_<stage>.parquet` | Probabilistic simulation distributions, quantile ranks, and DNF risks. |
| `models/` | `<run_id>/model.joblib`, `metadata.json` | Serialized model parameters, calibration weights, and data fingerprints. |
| `reports/` | `walk_forward_<session>_<year>_rnd<rnd>_<report>.parquet` | Brier scores, reliability diagrams, log-loss, and baseline comparisons. |

---

## Testing & Quality Assurance

Execute the complete test suite to verify analytical correctness, temporal isolation, and API stability:

```bash
# Execute fast test discovery across all packages
pytest -q

# Run targeted module test suites
python -m unittest discover -s tests/models
python -m unittest discover -s tests/pipeline
python -m unittest discover -s tests/services
```

### Frontend Validation
```bash
cd web
npm run typecheck
npm run build
```

---

## Troubleshooting & Diagnostics

| Symptom / Error | Root Cause | Resolution |
| :--- | :--- | :--- |
| `Target year is rejected` | Requested year is prior to 2021. | Specify season year $\ge 2021$ for the prediction CLI. |
| `No requested session or entrants` | Target event has no confirmed entrants in FastF1. | Ensure qualifying or sprint sessions have occurred, or specify valid past events. |
| `Fingerprint mismatch / Stale model` | Source data or code changed since model was trained. | Retrain model using `python main.py train <session> <year> "<event>"`. |
| `Database connection refused` | PostgreSQL container is initializing or down. | Run `docker compose ps` and check logs with `docker compose logs api`. |
| `Evaluation needs 9 events` | Insufficient completed historical races for walk-forward splits. | Expand historical collection budget (`--events-per-season`) or collect more seasons. |
| `Worker lock contention` | Another prediction or import job holds the PostgreSQL lock. | Check active jobs with `GET /api/jobs` or inspect `docker compose logs worker`. |

---

## Roadmap & Future Milestones

* **Lap-Level Dynamic Strategy Simulation**: Transition from static stochastic sampling to lap-by-lap tyre degradation and pit window simulation.
* **Weather Radar Spatial Modeling**: Incorporate real-time track sector precipitation probabilities.
* **Expanded Historical Range**: Extend point-in-time feature calibration back to the 2014 hybrid era introduction.
* **Automated Experiment Tracking**: Integrate optional MLflow and Optuna hyperparameter tracking pipelines.

---

## Data Sources & Attribution

* **[FastF1](https://docs.fastf1.dev/)**: Session timing, telemetry, weather streams, and lap charts.
* **[FIA](https://www.fia.com/) & [Formula 1](https://www.formula1.com/)**: Official sporting regulations, entry lists, and race classifications.
* **[Jolpica F1 API](https://github.com/jolpica/jolpica-f1)**: Ergast-compatible championship standings and historical results.
* **[OpenTelemetry](https://opentelemetry.io/) & [Grafana](https://grafana.com/)**: Application and model observability instrumentation.

The MIT licence covers application code, not upstream datasets. Jolpica's
[terms of use](https://github.com/jolpica/jolpica-f1/blob/main/TERMS.md)
specify non-commercial use under
[CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/);
commercial use requires contacting the provider. The UI provides attribution and
an unofficial-project disclaimer and uses the bundled Noto Sans font, not a
proprietary Formula 1 font. These changes do not grant rights to timing data or
trademarks. Operator review of the intended public/commercial use remains required.

---
*For further technical inquiries, review [CONTRIBUTING.md](../CONTRIBUTING.md) and [SECURITY.md](../SECURITY.md).*
