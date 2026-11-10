# F1 Race Intelligence & Winner Prediction

> An end-to-end Formula 1 data engineering, machine learning and
> race-visualisation project. The first target is a pre-race prediction
> for the 2026 São Paulo Grand Prix; the platform is designed to
> generalise to every Grand Prix.

![Python](https://img.shields.io/badge/Python-3.12%2B-blue)
![Data](https://img.shields.io/badge/Data-Parquet-orange)
![ML](https://img.shields.io/badge/ML-Scikit--learn%20%7C%20XGBoost-green)
![Tracking](https://img.shields.io/badge/Experiments-MLflow-purple)
![UI](https://img.shields.io/badge/UI-Streamlit-red)

------------------------------------------------------------------------

## Contents

-   [Project vision](#project-vision)
-   [What exists today](#what-exists-today)
-   [End-to-end architecture](#end-to-end-architecture)
-   [Technology strategy](#technology-strategy)
-   [Requirements and local setup](#requirements-and-local-setup)
-   [Data collection](#data-collection)
-   [Feature engineering](#feature-engineering)
-   [Planned feature catalogue](#planned-feature-catalogue)
-   [Prediction strategy](#prediction-strategy)
-   [Validation and model evaluation](#validation-and-model-evaluation)
-   [Deep learning research track](#deep-learning-research-track)
-   [Experiment tracking and
    Databricks](#experiment-tracking-and-databricks)
-   [Interactive F1 analytics
    experience](#interactive-f1-analytics-experience)
-   [Wikipedia and Wikimedia Commons
    enrichment](#wikipedia-and-wikimedia-commons-enrichment)
-   [Proposed repository structure](#proposed-repository-structure)
-   [Roadmap](#roadmap)
-   [Reproducibility, quality and
    governance](#reproducibility-quality-and-governance)
-   [Limitations and modelling
    principles](#limitations-and-modelling-principles)
-   [References](#references)

## Project vision

Build a reliable, explainable and visually rich F1 analytics platform
that estimates each driver's probability of winning a Grand Prix before
lights out.

The project combines:

1.  **Motorsport data engineering** --- multi-season, multi-session
    collection and historical data preparation.
2.  **Point-in-time feature engineering** --- driver, constructor,
    circuit, qualifying, practice, tyre, weather and reliability
    features computed only from information available at the prediction
    timestamp.
3.  **Machine learning** --- interpretable baselines, gradient-boosted
    trees, calibrated probability models and carefully justified
    deep-learning experiments.
4.  **Race simulation** --- scenario analysis for safety cars, weather,
    reliability and pace uncertainty.
5.  **Visual analytics** --- a polished Streamlit application with
    driver and constructor profiles, circuit information, track
    visualisations, predictions and model explanations.

### Primary objective

For a selected Grand Prix and a defined pre-race cutoff time, estimate:

-   Each driver's probability of winning.
-   Each driver's probability of finishing on the podium.
-   Expected finishing-position distribution.
-   The most influential factors behind each prediction.
-   Prediction uncertainty under different race scenarios.

### Initial case study

**2026 São Paulo Grand Prix --- Interlagos**

The 2026 event is a retrospective case study because the race has
already taken place. The system must reconstruct the information
available at a clearly specified pre-race cutoff. The actual race
classification is reserved for evaluation and must never enter the
pre-race feature set.

The long-term objective is a reusable model for all Grand Prix weekends,
not a model trained only on Brazilian Grand Prix results.

------------------------------------------------------------------------

## What exists today

The current repository contains a FastF1-based session collection
pipeline and a feature-engineering entry point.

### Current collection capabilities

The collector supports scheduled:

-   Practice sessions.
-   Qualifying.
-   Sprint qualifying.
-   Sprint.
-   Race.

By default, it processes seasons **2018--2026, excluding 2020**. It also
supports explicit years, inclusive year ranges and event-specific
collection.

For each collected year/session, the pipeline writes Parquet datasets
under `data/raw`:

-   `*_results.parquet`
-   `*_laps.parquet`
-   `*_weather.parquet`
-   `*_messages.parquet`
-   `*_schedule.parquet`

Rows include event metadata such as year, round, event name, session
type and scheduled UTC session start when FastF1 supplies it.

A failure or future start in one session should not prevent other
sessions from being attempted or overwrite previously saved files.

### Current feature-engineering capabilities

Run:

``` bash
python -m src.features.engineer_features
```

The pipeline writes separate feature datasets under `data/processed`
when the relevant target sessions exist:

-   `race_features.parquet`
-   `sprint_features.parquet`
-   `qualifying_features.parquet`
-   `sprint_qualifying_features.parquet`

The current pipeline is designed around point-in-time correctness:

-   Historical driver and constructor information is restricted to
    sessions that started strictly before the target session.
-   Same-weekend practice, lap and tyre summaries are eligible only when
    their source session started before the target session.
-   Weather observations and race-control messages are filtered using
    the same chronological rule.
-   Target outcomes are represented by `target_*` columns and must not
    be used as model inputs.
-   Same-session classifications are not features for their own target
    session.

### Driver and constructor inventories

The collection command refreshes:

-   `data/processed/drivers.csv`
-   `data/processed/constructors.csv`

These inventories are built from all existing `*_results.parquet` files,
including older race-only files and sessions outside the latest
collection request. They are identity inventories, not a complete
career-statistics database. Career and season statistics should be
derived as point-in-time features from historical results.

### Optional timestamped news input

The pipeline can consume `data/raw/news.csv`.

Accepted publication timestamp columns:

-   `PublishedAt`
-   `published_at`
-   `PublishedUTC`

The file should identify the event using `EventName` or `CircuitName`,
or provide a `Year`. Only reports published before the target session
start contribute to news counts. The pipeline does not fetch, infer or
backdate news.

### Known coverage constraints

-   FastF1 does not provide every session type, lap, weather observation
    or race-control message for every season.
-   Missing evidence must remain missing; do not fabricate telemetry or
    weather.
-   Legacy files without session timestamps may use saved schedule
    timestamps. If unavailable, the deterministic year/round/session
    fallback cannot recover the true time of day and is less precise.
-   Circuit identity may use FastF1 `Location`, falling back to
    `EventName`. Event names and circuit identities can change; a
    curated alias map may be required.
-   Collection and feature generation should be rerun separately if
    collection reports a failure after attempting the requested seasons.

------------------------------------------------------------------------

## End-to-end architecture

``` mermaid
flowchart TD
    A[FastF1 session data] --> B[Raw Parquet lake]
    C[Optional timestamped news] --> B
    D[Wikipedia / Wikimedia Commons metadata] --> V[Presentation metadata store]

    B --> E[Data quality and session normalisation]
    E --> F[Point-in-time feature engineering]
    F --> G[Processed feature Parquet]
    G --> H[Training and validation datasets]

    H --> I[Baseline models]
    H --> J[Gradient boosting]
    H --> K[Deep learning research]
    I --> L[Probability calibration and race-level reconciliation]
    J --> L
    K --> L

    L --> M[MLflow experiments and model registry]
    M --> N[Prediction and scenario engine]
    N --> O[Streamlit analytics application]
    V --> O
```

### Data layers

  -----------------------------------------------------------------------
  Layer                   Purpose                 Format / technology
  ----------------------- ----------------------- -----------------------
  Raw                     Immutable collected     Parquet
                          session data            

  Normalised              Consistent driver,      Parquet
                          constructor, circuit    
                          and session identities  

  Processed               Point-in-time features  Parquet
                          and target labels       

  Training                Versioned model-ready   Parquet
                          datasets                

  Experiments             Parameters, metrics,    MLflow
                          models and artefacts    

  Presentation            Biographies, images,    JSON / SQLite / cached
                          citations and visual    API responses
                          metadata                
  -----------------------------------------------------------------------

Keep raw data separate from processed features. Feature generation
should be repeatable from raw data and a versioned feature
configuration.

------------------------------------------------------------------------

## Technology strategy

The development machine is a **MacBook Air M3 with 16 GB unified
memory**. Local-first development is the default. Cloud infrastructure
is introduced only when it provides a clear benefit.

  -----------------------------------------------------------------------
  Area                    Recommended tools       Role
  ----------------------- ----------------------- -----------------------
  Language                Python 3.12+            Main development
                                                  language

  IDE                     PyCharm Professional    Development, debugging
                                                  and notebooks

  Data ingestion          FastF1, Requests        F1 session and external
                                                  metadata ingestion

  Data processing         Pandas, Polars, NumPy   Data cleaning and
                                                  feature engineering

  Storage                 Parquet, PyArrow,       Efficient local
                          DuckDB                  analytical data

  Classical ML            Scikit-learn            Pipelines, baselines,
                                                  validation and
                                                  calibration

  Gradient boosting       XGBoost, LightGBM,      Primary tabular model
                          CatBoost                candidates

  Hyperparameter tuning   Optuna                  Search, pruning and
                                                  reproducible tuning

  Explainability          SHAP, permutation       Global and per-driver
                          importance              explanations

  Experiment tracking     MLflow                  Runs, metrics,
                                                  parameters, artefacts
                                                  and model versions

  Deep learning           PyTorch, PyTorch        Neural-network research
                          Lightning               track

  Tabular DL research     FT-Transformer / TabNet Compare neural tabular
                          implementations         models with boosting

  Visualisation           Plotly, Matplotlib      Interactive and static
                                                  charts

  Application             Streamlit               Interactive F1
                                                  analytics product

  Testing                 Pytest, Pandera         Unit tests and data
                                                  contracts

  Code quality            Ruff, pre-commit        Linting and formatting

  Workflow orchestration  Prefect or Dagster      Scheduled and
                          (later)                 observable pipelines

  Cloud ML platform       Databricks              Managed data, feature
                          (later/optional)        and model lifecycle

  Optional API            FastAPI                 Serve predictions
                                                  separately from the UI
  -----------------------------------------------------------------------

### Recommended implementation order

Do not install and operate every platform on day one.

**Local foundation**

-   Python, FastF1, Pandas/Polars, DuckDB and Parquet.
-   Scikit-learn, XGBoost, LightGBM and CatBoost.
-   Optuna and SHAP.
-   MLflow local tracking.
-   Streamlit and Plotly.

**Research expansion**

-   PyTorch and PyTorch Lightning.
-   FT-Transformer or another tabular deep-learning baseline.
-   Monte Carlo race simulation.
-   More sophisticated probability calibration and uncertainty
    estimation.

**Cloud and scale**

-   Databricks, Unity Catalog and Feature Engineering when collaborative
    workflows, larger data or managed governance justify them.
-   MLflow remote tracking and model registry.
-   Cloud object storage and scheduled orchestration.
-   Model serving or FastAPI if the application needs a separate
    inference service.

The MacBook Air is sufficient for this project's local data engineering
and CPU-based tabular models. Do not assume that CUDA GPU acceleration
is available on Apple Silicon. For large deep-learning experiments, use
a compatible cloud GPU environment rather than trying to force CUDA onto
the Mac.

------------------------------------------------------------------------

## Requirements and local setup

### 1. Prerequisites

-   macOS on Apple Silicon.
-   Python 3.12 or a compatible project Python version.
-   Git.
-   PyCharm Professional.
-   Homebrew is recommended for package management.

Check the environment:

``` bash
python3 --version
git --version
```

### 2. Create and activate a virtual environment

From the repository root:

``` bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

### 3. Install the local development stack

``` bash
pip install \
  fastf1 \
  pandas \
  polars \
  numpy \
  pyarrow \
  duckdb \
  scikit-learn \
  xgboost \
  lightgbm \
  catboost \
  optuna \
  shap \
  mlflow \
  matplotlib \
  plotly \
  streamlit \
  requests \
  pydantic \
  pandera \
  pytest \
  ruff \
  jupyter
```

Install deep-learning packages as a separate research dependency group:

``` bash
pip install torch lightning
```

Check the PyTorch device:

``` python
import torch

print("PyTorch:", torch.__version__)
print("MPS available:", torch.backends.mps.is_available())
print("CUDA available:", torch.cuda.is_available())
```

Apple Silicon may use the PyTorch MPS backend for compatible operations.
MPS support and operator coverage should be tested for the specific
model and package versions.

### 4. Configure PyCharm

1.  Open the repository in PyCharm.
2.  Set the project interpreter to `.venv/bin/python`.
3.  Mark `src` as a source root if required by the project
    configuration.
4.  Use the repository root as the working directory for module
    commands.
5.  Configure environment variables in a local `.env` file only when
    required.
6.  Keep secrets, tokens and local credentials out of Git.

### 5. Dependency management

After the environment is stable, pin tested dependencies in a lock file
or use a dependency manager such as `uv`. Avoid relying on unpinned
package versions for reproducible experiments.

------------------------------------------------------------------------

## Data collection

All commands below are run from the repository root.

### Default collection

Collect scheduled practice, qualifying, sprint qualifying, sprint and
race sessions for 2018--2026, excluding 2020:

``` bash
python -m src.data.collect_season
```

### Collect an inclusive year range

``` bash
python -m src.data.collect_season \
  --start-year 2018 \
  --end-year 2026
```

A range includes every year between its endpoints, including 2020.

### Collect one event across a year range

``` bash
python -m src.data.collect_season \
  --start-year 2021 \
  --end-year 2026 \
  --event "Brazilian Grand Prix"
```

### Collect non-contiguous seasons

Use `--year` more than once:

``` bash
python -m src.data.collect_season \
  --year 2018 \
  --year 2019 \
  --year 2021 \
  --year 2022 \
  --year 2023 \
  --year 2024 \
  --year 2025 \
  --year 2026
```

Do not combine `--year` with range options. Both `--start-year` and
`--end-year` are required when using a range.

### Event lookup

Event lookup accepts a scheduled event name, location or country,
ignoring case, accents and a trailing `Grand Prix` / `GP`.
`Brazilian Grand Prix` is recognised as Brazil. If a location or country
matches multiple events, use the exact scheduled event name to select
the intended round.

### Data collection principles

-   Use schedule round numbers after resolving the event; do not rely on
    event names being identical across seasons.
-   Preserve raw source outputs.
-   Cache FastF1 data.
-   Log failed sessions and retry them deliberately.
-   Do not replace a valid existing file with an incomplete download.
-   Record collection time, source, season, session and data version
    where practical.

------------------------------------------------------------------------

## Feature engineering

Run after collection:

``` bash
python -m src.features.engineer_features
```

The feature pipeline creates target-specific feature tables for race,
sprint, qualifying and sprint qualifying.

### Point-in-time rule

Every feature row must represent what was knowable at the target
session's scheduled start.

For target session time `T`, a source observation is eligible only when:

``` text
source_session_start_utc < T
```

This strict comparison is essential. A session starting at the same time
or later is not historical information for the target.

### Target columns

Target outcomes are stored in `target_*` columns. They are labels, not
model inputs.

The model training code must explicitly separate:

-   Entity and grouping keys.
-   Prediction-time features.
-   Target labels.
-   Metadata and audit columns.

Never pass all columns to a model automatically.

### Optional news features

If `data/raw/news.csv` exists, the pipeline may use its timestamped
reports. Only reports published before the target session start
contribute to news counts. News collection is not performed by the
feature pipeline.

### Feature groups to develop

  -----------------------------------------------------------------------
  Feature family          Example features        Source
  ----------------------- ----------------------- -----------------------
  Driver form             Rolling points, wins,   Earlier race results
                          podiums,                
                          finishing-position      
                          distribution            

  Driver experience       Previous starts,        Earlier race results
                          career-to-date          
                          statistics              

  Qualifying              Grid position, Q1/Q2/Q3 Earlier qualifying
                          pace, gap to pole       session / target
                                                  weekend qualifying

  Teammate comparison     Driver pace and         Same team and earlier
                          qualifying delta to     eligible sessions
                          teammate                

  Constructor form        Rolling team points,    Earlier team results
                          wins, podiums, average  
                          pace                    

  Reliability             DNF rate, completed-lap Earlier race sessions
                          ratio, mechanical       
                          retirement history      

  Practice pace           Clean-lap pace,         Earlier practice
                          representative long-run sessions
                          pace                    

  Tyres                   Compound, stint length, Earlier eligible lap
                          degradation estimates   data

  Circuit                 Circuit identity,       Circuit metadata and
                          layout characteristics, earlier events
                          historical performance  

  Weather                 Air/track temperature,  Earlier weather
                          rainfall and wind       observations
                          observations            

  Race control            Safety-car / VSC /      Earlier messages
                          red-flag history and    
                          event context           

  News                    Pre-session news counts Timestamped news input
                          or carefully validated  
                          text signals            
  -----------------------------------------------------------------------

Not every feature is available for every season. Missingness should be
represented explicitly and handled consistently.

### Driver and constructor master data

The existing inventories are refreshed by the collection command:

-   `data/processed/drivers.csv`
-   `data/processed/constructors.csv`

Next, build point-in-time driver and constructor feature tables. Do not
confuse an identity inventory with a complete career profile.

Recommended entity keys:

-   Driver: stable driver identifier, with abbreviation as a display
    attribute.
-   Constructor: stable constructor identifier, with team name as a
    time-varying display attribute.
-   Event: season + round + event identity.
-   Session: event identity + session type + session start timestamp.

Maintain historical driver-to-constructor relationships. A driver's
constructor can change between seasons or even within a season.

------------------------------------------------------------------------

## Planned feature catalogue

The following is the target feature roadmap. These features should be
added only after their source coverage, timestamp semantics and
calculation are tested.

### Driver features

-   Career starts, wins, podiums and points before the target.
-   Rolling 3-, 5- and 10-race form.
-   Rolling qualifying position and qualifying gap.
-   Finish-position variance and consistency.
-   DNF and mechanical retirement rates.
-   Teammate-relative qualifying and race pace.
-   Circuit-specific historical performance.
-   Wet-weather performance, where sample size permits.
-   Driver experience and season tenure.

### Constructor features

-   Constructor points and championship standing before the target.
-   Rolling team points, wins and podiums.
-   Average qualifying and race pace.
-   Team reliability and completed-lap ratio.
-   Pit-stop performance where reliable timing data exists.
-   Driver pairing strength.
-   Season-to-date performance trajectory.

### Weekend and circuit features

-   Actual grid position after penalties.
-   Qualifying gap to pole and teammate.
-   Practice pace relative to the field.
-   Long-run pace and degradation.
-   Track temperature and weather.
-   Circuit length, layout and corner characteristics.
-   Historical overtaking and safety-car characteristics, where
    supported by data.
-   Sprint weekend format and sprint-derived information only when the
    sprint occurred before the target session.

### Pace normalisation

Raw lap times are not directly comparable across circuits, years,
weather and regulation eras.

Prefer features such as:

-   Driver pace relative to teammate in the same session.
-   Driver pace relative to the session median.
-   Percentage gap to the fastest representative pace.
-   Long-run pace adjusted for tyre compound, stint age and track
    conditions.
-   Clean-lap pace excluding pit in/out laps, safety-car periods, red
    flags and obvious outliers.

Document each exclusion rule. Do not remove slow laps simply because
they make a driver look less competitive.

### Regulation-era handling

F1 technical regulations and car performance change over time. Include
season and regulation-era context, test whether older seasons help, and
compare models trained on different historical windows. Avoid assuming
that a constructor's historical pace transfers unchanged to a new
regulation cycle.

------------------------------------------------------------------------

## Prediction strategy

### Training unit

For race-winner prediction, create one row per driver per Grand Prix:

``` text
season | round | driver_id | constructor_id | prediction_cutoff
feature_1 | feature_2 | ... | target_win
```

All drivers in one race form a prediction group. The target is:

``` text
target_win = 1 if the driver won the race else 0
```

The race group must contain the eligible starters and exactly one winner
for a normally classified race. Handle exceptional classifications
explicitly.

### Prediction tasks

  -----------------------------------------------------------------------
  Task                    Formulation             Output
  ----------------------- ----------------------- -----------------------
  Winner                  Grouped race-level      Win probability per
                          probability estimation  driver

  Podium                  Driver-level            Podium probability
                          classification          

  Finishing position      Regression or ranking / Expected position and
                          distribution model      uncertainty

  Scenario simulation     Monte Carlo simulation  Outcome distributions
                                                  under scenarios
  -----------------------------------------------------------------------

### Model ladder

Start with simple, auditable baselines before more complex models.

  ------------------------------------------------------------------------------
  Model                   Purpose                        Priority
  ----------------------- ------------------------------ -----------------------
  Historical /            Establish a simple benchmark   Must-have
  championship baseline                                  

  Logistic Regression     Interpretable probability      Must-have
                          baseline                       

  Random Forest           Nonlinear baseline             Must-have

  XGBoost                 Strong gradient-boosted        Must-have
                          tabular candidate              

  LightGBM                Efficient boosted-tree         High
                          comparison                     

  CatBoost                Categorical-feature-friendly   High
                          boosted-tree comparison        

  Calibrated ensemble     Combine validated model        High
                          probabilities                  

  PyTorch MLP             Neural tabular baseline        Research

  FT-Transformer / TabNet Advanced tabular deep-learning Research
                          comparison                     

  Sequence model          Learn from ordered driver/team Later research
                          race histories                 
  ------------------------------------------------------------------------------

No algorithm is guaranteed to be the best. Select models based on
chronological out-of-sample performance, probability quality and
calibration---not training accuracy.

### Race-level probability consistency

Independent binary classifiers can output probabilities that do not sum
to one across the drivers in a race.

The prediction layer must therefore evaluate race-level probability
approaches, such as:

-   A multiclass model with one race group as the classification unit.
-   A grouped or listwise model.
-   A calibrated transformation of driver-level scores into a race-level
    distribution.

Do not blindly normalise probabilities and assume they are calibrated.
Evaluate the resulting race-level probabilities on held-out races.

### Ensemble strategy

Candidate ensemble:

-   Logistic Regression for a stable baseline.
-   XGBoost / LightGBM / CatBoost for nonlinear tabular patterns.
-   Optional neural model only if it adds repeatable out-of-sample
    value.
-   Calibration on a validation period that is separate from final
    testing.

Use out-of-fold or chronological validation predictions to learn
ensemble weights. Never choose weights using the final test period.

### Monte Carlo race simulation

A simulation engine can complement the ML model. Simulate thousands of
race scenarios by sampling uncertain variables such as:

-   Relative pace.
-   Reliability / retirement events.
-   Pit-stop loss.
-   Safety-car and VSC scenarios.
-   Weather changes.
-   Strategy and tyre degradation.

The simulator should be a separate, testable component. Its assumptions
must be documented and calibrated against historical races. It is not a
substitute for a validated predictive model.

------------------------------------------------------------------------

## Validation and model evaluation

### Chronological validation

Do not randomly split individual driver rows. Drivers from the same race
belong to the same group, and future races must not leak into past
training.

Use expanding-window or rolling-origin evaluation. Example:

  Training seasons                Validation / test season
  ------------------------------- --------------------------------------
  2018--2021                      2022
  2018--2022                      2023
  2018--2023                      2024
  2018--2024                      2025
  Earlier completed 2026 events   Later 2026 events, where appropriate

For a retrospective 2026 Brazil prediction, construct the target
features from data strictly before the declared pre-race cutoff. Keep
the actual 2026 Brazilian GP result outside training and feature
construction.

### Required metrics

**Winner probability**

-   Multiclass / grouped log loss.
-   Brier score.
-   Top-1 winner accuracy.
-   Top-3 winner coverage (whether the actual winner appears in the
    model's top three).
-   Calibration plots.

**Podium prediction**

-   Precision, recall and F1.
-   Average precision / PR-AUC.
-   Brier score and calibration.

**Finishing position**

-   Mean absolute error.
-   Spearman rank correlation.
-   Top-three ordering metrics where appropriate.

### Baselines

Compare every model against:

-   Pole-position winner baseline.
-   Championship leader baseline.
-   Recent-form baseline.
-   Constructor-strength baseline.
-   Simple qualifying-position model.

### Model selection principles

-   Evaluate at the race level, not just the row level.
-   Report results by season, circuit and regulation era.
-   Use confidence intervals or bootstrap estimates where practical.
-   Check probability calibration.
-   Record the feature set, data cutoff, training window and code
    version for every experiment.
-   Keep the final test season untouched until model selection is
    complete.

------------------------------------------------------------------------

## Deep learning research track

Deep learning is an experiment, not an assumption. This project has a
relatively small number of independent Grand Prix events compared with
typical deep-learning applications. Gradient-boosted trees may
outperform neural networks on the available tabular data.

### Phase 1 --- Tabular neural baseline

Use PyTorch to build a compact multilayer perceptron (MLP) with:

-   Normalised numerical features.
-   Properly embedded categorical features.
-   Dropout and weight decay.
-   Early stopping.
-   Race-group-aware evaluation.

### Phase 2 --- Advanced tabular architectures

Evaluate an FT-Transformer or TabNet implementation against XGBoost,
LightGBM and CatBoost using identical chronological splits and features.

### Phase 3 --- Sequential driver and constructor histories

If enough historical observations are available, research:

-   GRU / LSTM encoders for ordered race histories.
-   Temporal attention models.
-   Transformer encoders for driver and constructor sequences.

Avoid a large Temporal Fusion Transformer or complex sequence
architecture until the dataset size, target definition and validation
protocol justify it.

### Phase 4 --- Multimodal research

Potential future inputs:

-   Structured race and timing data.
-   Telemetry-derived time-series features.
-   Timestamped pre-race news.
-   Weather forecasts available at the cutoff.

Keep the prediction-time contract strict. A news article, weather
observation or telemetry segment published or recorded after the cutoff
cannot be used.

### Apple Silicon and cloud compute

Use the Mac's CPU and compatible MPS operations for development. Use
cloud GPU compute for larger neural experiments if required. Record
device, precision, package versions and random seeds.

------------------------------------------------------------------------

## Experiment tracking and Databricks

### MLflow --- start locally

Use MLflow from the beginning to track:

-   Model family and hyperparameters.
-   Training and validation periods.
-   Feature-set version.
-   Dataset fingerprints.
-   Metrics and calibration artefacts.
-   Model files and prediction outputs.
-   Git commit and environment details.

Start the local tracking UI:

``` bash
mlflow server --host 127.0.0.1 --port 5000
```

Open:

``` text
http://127.0.0.1:5000
```

Use a separate MLflow experiment for each modelling objective, for
example:

-   `f1-race-winner`
-   `f1-podium`
-   `f1-finishing-position`
-   `f1-race-simulation`

### Optuna

Use Optuna for controlled hyperparameter optimisation. Keep the search
space bounded, use chronological validation, log each trial and use
pruning where appropriate.

### Databricks --- optional scale-up

Databricks is not required for the first local implementation. Introduce
it when the project needs:

-   Collaborative notebooks and managed compute.
-   Larger-scale feature transformations.
-   Governed feature tables and lineage.
-   Managed model lifecycle and deployment.
-   Shared cloud storage and scheduled pipelines.

Potential architecture:

-   Delta Lake for curated analytical tables.
-   Unity Catalog for governance and lineage.
-   Databricks Feature Engineering / Feature Store for reusable features
    and point-in-time training sets.
-   MLflow for experiment tracking and model lifecycle.
-   Databricks jobs or Lakeflow pipelines for scheduled workflows.

Keep feature definitions portable so the local Parquet/DuckDB workflow
remains usable. Do not migrate to Spark merely for the sake of using a
distributed platform.

------------------------------------------------------------------------

## Interactive F1 analytics experience

The user interface should feel like a premium motorsport analytics
product---not a notebook with charts.

### Recommended stack

-   Streamlit for application layout and navigation.
-   Plotly for interactive charts.
-   FastF1 for circuit and telemetry visualisations.
-   Wikipedia / Wikimedia APIs for contextual editorial information and
    images.
-   Local metadata cache for repeatable, fast page loads.

### Application information architecture

``` mermaid
flowchart TD
    A[F1 Race Intelligence] --> B[Race Prediction]
    A --> C[Race Weekend]
    A --> D[Driver Profiles]
    A --> E[Constructor Profiles]
    A --> F[Circuit Explorer]
    A --> G[Model Lab]

    B --> B1[Win probability]
    B --> B2[Podium probability]
    B --> B3[Expected finishing positions]
    B --> B4[Scenario simulation]

    C --> C1[Practice pace]
    C --> C2[Qualifying]
    C --> C3[Tyre strategy]
    C --> C4[Weather and race control]

    D --> D1[Biography and image]
    D --> D2[Career statistics]
    D --> D3[Recent form]
    D --> D4[Teammate comparison]

    E --> E1[Constructor profile]
    E --> E2[Season performance]
    E --> E3[Reliability and pace]

    F --> F1[Track image and facts]
    F --> F2[Track layout]
    F --> F3[Corner and sector analysis]

    G --> G1[Model comparison]
    G --> G2[Calibration]
    G --> G3[SHAP explanations]
```

### Screen 1 --- Race prediction dashboard

The landing page should include:

-   Grand Prix name, season, date and circuit hero section.
-   Track image and circuit facts.
-   Driver win-probability leaderboard.
-   Podium probability chart.
-   Predicted finishing-position distribution.
-   Top prediction factors.
-   Model confidence and last training timestamp.
-   Clear pre-race prediction cutoff timestamp.

### Screen 2 --- Race weekend analytics

-   Practice-session lap-time evolution.
-   Qualifying pace and gap to pole.
-   Teammate qualifying comparison.
-   Driver pace distribution.
-   Tyre compound and stint analysis.
-   Weather observations and available forecast snapshots.
-   Race-control event timeline.

### Screen 3 --- Driver profile

-   Driver portrait.
-   Full name, nationality and current constructor.
-   Biography and career summary.
-   Career statistics up to a selected date.
-   Recent performance trend.
-   Qualifying versus teammate.
-   Circuit-specific record.
-   Model win and podium probabilities.

### Screen 4 --- Constructor profile

-   Constructor/team image or logo where licensing permits.
-   Team description and base/location.
-   Current drivers.
-   Championship position and points trend.
-   Qualifying and race pace.
-   Reliability statistics.
-   Pit-stop performance where available.
-   Driver pairing comparison.

### Screen 5 --- Circuit explorer

-   Circuit hero image.
-   Track name, location and country.
-   Circuit length, corner count and lap record where verified.
-   Circuit layout generated from FastF1 telemetry where available.
-   Sector and corner visualisations.
-   Historical race trends.
-   Weather and overtaking context.

### Screen 6 --- Model laboratory

-   Model leaderboard.
-   Chronological backtest results.
-   Log loss and Brier score.
-   Probability calibration.
-   Feature importance and SHAP plots.
-   Experiment comparison.
-   Prediction versus actual result history.

### UI design direction

Use a dark motorsport-inspired visual system:

-   Charcoal / near-black backgrounds.
-   Strong red accent for key race actions.
-   White and muted-grey typography.
-   Team colours as controlled accents.
-   Large driver and circuit photography.
-   Clear data hierarchy and compact analytical cards.
-   Responsive desktop-first layout, with mobile support.
-   Consistent chart scales, legends, units and tooltips.

Avoid making the application a wall of charts. Each visual should answer
a specific racing question.

------------------------------------------------------------------------

## Wikipedia and Wikimedia Commons enrichment

Use Wikipedia for contextual information and Wikimedia Commons for
reusable media, while keeping F1 sporting data sourced from FastF1 and
other authoritative motorsport sources.

### Information to enrich

  -----------------------------------------------------------------------
  Entity                  Contextual information  Visual
  ----------------------- ----------------------- -----------------------
  Circuit                 Location, history,      Circuit photograph
                          track description       

  Driver                  Biography, nationality, Driver portrait
                          career summary          

  Constructor             Team history, base and  Team-related photograph
                          background              

  Race                    Grand Prix overview and Race / venue image
                          historical context      
  -----------------------------------------------------------------------

### Recommended implementation

1.  Resolve a stable entity identifier or curated Wikipedia page title.
2.  Retrieve a short page summary through the MediaWiki API.
3.  Search Wikimedia Commons for suitable images.
4.  Retrieve the original file URL and image metadata.
5.  Store the author, license, attribution, source page and retrieval
    timestamp.
6.  Cache responses to avoid repeated API requests.
7.  Render image credits and source links in the UI.

### Media metadata schema

``` json
{
  "entity_type": "driver",
  "entity_id": "driver_identifier",
  "display_name": "Driver Name",
  "wikipedia_url": "https://en.wikipedia.org/wiki/...",
  "summary": "Short sourced biography",
  "image_url": "https://upload.wikimedia.org/...",
  "commons_file_page": "https://commons.wikimedia.org/wiki/File:...",
  "creator": "Photographer name",
  "license": "License identifier",
  "attribution": "Required attribution text",
  "retrieved_at": "ISO-8601 timestamp"
}
```

The values above are a schema example, not actual driver metadata.

### Attribution and licensing

-   Prefer Wikimedia Commons files with clear reuse terms.
-   Check the licence of each individual image; not all images share the
    same licence.
-   Display attribution and licence information near the image or in an
    accessible credits panel.
-   Preserve the original Commons file page URL.
-   Follow the applicable licence conditions, including attribution and
    share-alike requirements where relevant.
-   Respect API usage policies and provide an identifiable User-Agent.
-   Do not assume that a Wikipedia article's text licence automatically
    applies to every image embedded in it.
-   Treat team and championship logos as potentially trademarked assets,
    even when an image file is accessible.

### Data-source separation

Wikipedia is an editorial enrichment source, not the authority for race
results, grid penalties, live timing or championship calculations.

Use:

-   FastF1 for timing, telemetry and session information.
-   Official F1 / FIA sources for regulations and official sporting
    decisions.
-   Wikipedia for concise historical context.
-   Wikimedia Commons for individually verified, reusable images.

------------------------------------------------------------------------

## Proposed repository structure

The following is the target structure. Preserve the current modules and
adapt incrementally rather than moving files unnecessarily.

``` text
f1-race-intelligence/
│
├── data/
│   ├── raw/
│   ├── processed/
│   ├── training/
│   ├── predictions/
│   ├── external/
│   └── cache/
│
├── notebooks/
│   ├── 01_data_quality.ipynb
│   ├── 02_exploratory_analysis.ipynb
│   ├── 03_driver_analysis.ipynb
│   ├── 04_constructor_analysis.ipynb
│   ├── 05_feature_analysis.ipynb
│   └── 06_model_research.ipynb
│
├── src/
│   ├── data/
│   │   ├── collect_season.py
│   │   ├── validate_data.py
│   │   └── normalise_entities.py
│   │
│   ├── features/
│   │   ├── engineer_features.py
│   │   ├── driver_features.py
│   │   ├── constructor_features.py
│   │   ├── circuit_features.py
│   │   ├── pace_features.py
│   │   └── feature_registry.py
│   │
│   ├── models/
│   │   ├── baselines.py
│   │   ├── train_winner.py
│   │   ├── train_podium.py
│   │   ├── train_position.py
│   │   ├── calibrate.py
│   │   └── evaluate.py
│   │
│   ├── simulation/
│   │   ├── race_simulator.py
│   │   └── scenarios.py
│   │
│   ├── enrichment/
│   │   ├── wikipedia_client.py
│   │   ├── commons_client.py
│   │   └── media_metadata.py
│   │
│   ├── visualization/
│   │   ├── circuits.py
│   │   └── telemetry.py
│   │
│   └── utils/
│       ├── config.py
│       └── logging.py
│
├── app/
│   ├── app.py
│   ├── pages/
│   │   ├── 01_Race_Prediction.py
│   │   ├── 02_Race_Weekend.py
│   │   ├── 03_Drivers.py
│   │   ├── 04_Constructors.py
│   │   ├── 05_Circuit_Explorer.py
│   │   └── 06_Model_Lab.py
│   ├── components/
│   └── assets/
│
├── models/
├── reports/
├── tests/
├── configs/
├── docs/
│
├── requirements.txt
├── pyproject.toml
├── .gitignore
└── README.md
```

------------------------------------------------------------------------

## Roadmap

### Phase 1 --- Data foundation

-   [x] FastF1 season/session collection entry point.
-   [x] Multi-session Parquet output.
-   [x] Event schedule metadata.
-   [x] Driver and constructor identity inventories.
-   [x] Target-specific feature engineering entry point.
-   [x] Strict earlier-session filtering in feature generation.
-   [x] Optional timestamped news input.
-   [ ] Complete collection coverage audit.
-   [ ] Add data contracts and schema validation.
-   [ ] Create stable driver, constructor and circuit identity maps.
-   [ ] Record data and feature versions.

**Deliverable:** Trustworthy historical session and feature datasets.

### Phase 2 --- Advanced feature engineering

-   [ ] Driver career and rolling-form features.
-   [ ] Constructor season and rolling-form features.
-   [ ] Teammate-relative pace and qualifying features.
-   [ ] Clean-lap and long-run pace normalisation.
-   [ ] Tyre stint and degradation features.
-   [ ] Reliability and retirement classification.
-   [ ] Circuit history and circuit metadata.
-   [ ] Weather and race-control features.
-   [ ] Feature documentation and leakage tests.

**Deliverable:** A validated, point-in-time driver-race training
dataset.

### Phase 3 --- Baseline ML

-   [ ] Championship and qualifying baselines.
-   [ ] Logistic Regression.
-   [ ] Random Forest.
-   [ ] XGBoost.
-   [ ] LightGBM.
-   [ ] CatBoost.
-   [ ] Chronological backtesting.
-   [ ] Initial probability calibration.

**Deliverable:** Reproducible model comparison report.

### Phase 4 --- Advanced modelling

-   [ ] Optuna hyperparameter optimisation.
-   [ ] SHAP explanations.
-   [ ] Calibrated race-level probability model.
-   [ ] Ensemble experiments.
-   [ ] Finishing-position and podium models.
-   [ ] Monte Carlo race simulation.
-   [ ] PyTorch neural baseline.
-   [ ] FT-Transformer / TabNet experiments if justified.

**Deliverable:** Best validated model and uncertainty-aware predictions.

### Phase 5 --- Experiment management and scale

-   [ ] MLflow experiment tracking.
-   [ ] Dataset fingerprints and model artefacts.
-   [ ] Model registry workflow.
-   [ ] Optional Databricks workspace.
-   [ ] Optional Unity Catalog feature management.
-   [ ] Automated pipeline execution.

**Deliverable:** Reproducible model lifecycle.

### Phase 6 --- Visual analytics product

-   [ ] Streamlit design system and navigation.
-   [ ] Race prediction dashboard.
-   [ ] Driver and constructor profiles.
-   [ ] Circuit explorer.
-   [ ] Wikipedia and Commons enrichment.
-   [ ] Track layout and telemetry visualisations.
-   [ ] Model laboratory and SHAP explanations.
-   [ ] Responsive UI and performance optimisation.

**Deliverable:** A polished F1 analytics application.

### Phase 7 --- Retrospective 2026 Brazil case study

-   [ ] Define the pre-race prediction cutoff.
-   [ ] Build the prediction dataset using only eligible pre-race data.
-   [ ] Generate driver win and podium probabilities.
-   [ ] Generate finishing-position distributions.
-   [ ] Run scenario simulations.
-   [ ] Freeze and record the prediction artefacts.
-   [ ] Compare with actual race outcomes after prediction is frozen.
-   [ ] Publish a retrospective model report.

**Deliverable:** A fully documented prediction case study.

------------------------------------------------------------------------

## Reproducibility, quality and governance

### Data quality

Test for:

-   Duplicate driver/session records.
-   Missing driver or constructor identifiers.
-   Invalid or impossible lap times.
-   Inconsistent session timestamps.
-   Incorrect event and round mappings.
-   Missing or duplicated target labels.
-   Unexpected driver-to-constructor relationships.
-   Feature availability and missingness by season.

### Leakage prevention

Create automated tests asserting:

-   Every source session timestamp is strictly earlier than the target
    timestamp.
-   Target columns are excluded from the model feature matrix.
-   Race classification and post-race data cannot enter a pre-race
    feature set.
-   Historical rolling features are shifted before use.
-   Train and test races are separated chronologically.
-   All drivers from a race remain in the same evaluation group.
-   External news and weather data respect the prediction cutoff.

### Reproducibility metadata

Every model run should record:

``` text
experiment_id
run_id
git_commit
python_version
dependency_lock_hash
data_snapshot_id
feature_set_version
training_cutoff
validation_period
test_period
prediction_cutoff
random_seed
model_parameters
evaluation_metrics
```

### Testing

Use Pytest for unit and integration tests. Add tests for time-window
boundaries, rolling statistics, entity mappings, missing data and
race-level probability outputs.

Never publish a prediction without recording the model version, feature
version and data cutoff.

------------------------------------------------------------------------

## Limitations and modelling principles

1.  **Prediction is probabilistic.** Even a well-calibrated model cannot
    know the winner with certainty.
2.  **Small sample sizes matter.** Individual drivers and constructors
    have limited historical observations, especially across regulation
    changes.
3.  **Historical performance is not stationary.** Car regulations, team
    structures, drivers and circuits evolve.
4.  **Missing data is not zero.** Missing weather, telemetry or timing
    evidence must not be silently converted into a real measurement.
5.  **Deep learning is not automatically superior.** Boosted trees are
    strong candidates for structured tabular data and must be difficult
    to beat in honest validation.
6.  **Wikipedia is contextual.** Use authoritative motorsport sources
    for sporting facts and decisions.
7.  **Images require licensing checks.** Store and display source and
    attribution metadata.
8.  **The prediction cutoff is part of the model definition.** A
    qualifying-based prediction and a pre-qualifying prediction are
    different products.

------------------------------------------------------------------------

## References

### F1 data and technical sources

-   [FastF1 documentation](https://docs.fastf1.dev/)
-   [FastF1 GitHub repository](https://github.com/theOehrly/Fast-F1)
-   [FIA](https://www.fia.com/)
-   [Formula 1](https://www.formula1.com/)

### Machine learning

-   [Scikit-learn](https://scikit-learn.org/stable/)
-   [XGBoost](https://xgboost.readthedocs.io/)
-   [LightGBM](https://lightgbm.readthedocs.io/)
-   [CatBoost](https://catboost.ai/docs/)
-   [Optuna](https://optuna.readthedocs.io/)
-   [SHAP](https://shap.readthedocs.io/)
-   [PyTorch](https://pytorch.org/docs/stable/index.html)
-   [PyTorch Lightning](https://lightning.ai/docs/pytorch/stable/)

### Experiment tracking and data platforms

-   [MLflow Tracking](https://mlflow.org/docs/latest/ml/tracking/)
-   [Databricks Machine
    Learning](https://docs.databricks.com/aws/en/machine-learning/)
-   [Databricks Feature
    Store](https://docs.databricks.com/aws/en/machine-learning/feature-store/)

### Visualisation and application

-   [Streamlit documentation](https://docs.streamlit.io/)
-   [Plotly Python](https://plotly.com/python/)
-   [DuckDB](https://duckdb.org/docs/)
-   [Polars](https://docs.pola.rs/)

### Wikipedia and Wikimedia

-   [MediaWiki REST API](https://www.mediawiki.org/wiki/API:REST_API)
-   [Wikimedia Commons
    API](https://commons.wikimedia.org/wiki/Commons:API)
-   [Wikimedia attribution
    framework](https://www.mediawiki.org/wiki/Wikimedia_Attribution_Framework)
-   [Wikimedia Commons reuse
    guidance](https://commons.wikimedia.org/wiki/Commons:Reusing_content_outside_Wikimedia)

------------------------------------------------------------------------

## Project philosophy

**Data quality first. Point-in-time correctness always. Baselines before
complexity. Explainability alongside accuracy. Visual storytelling with
source attribution.**

The objective is not merely to guess the winner of one race. It is to
build a reusable, testable and explainable Formula 1 race-intelligence
platform.
