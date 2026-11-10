# F1 Prediction Pipeline Runbook

This guide covers running the prediction pipeline from the repository root. The command collects real FastF1 session data for the requested event, builds features from the 2021 season through the target year, trains the prediction models, and writes the requested session's predictions.

## Prerequisites

- Python 3.12 (or the compatible Python version configured for the project).
- Network access to retrieve FastF1 session and schedule data. The first collection can take a while; FastF1's cache and the project's raw-data files are reused where available.
- Run commands from the project root, where `main.py` and `requirements.txt` are located.

Create an environment and install the project dependencies:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

On subsequent runs, activate the environment first with `source .venv/bin/activate`.

## Run a prediction

The CLI takes exactly three positional arguments: session type, target year, and event name.

```text
python main.py <race|sprint|qualifying> <year> "<event name>"
```

The target year must be 2021 or later. For the Brazilian Grand Prix, use the event name recognized by the season schedule; the pipeline supports resolving `Brazilian Grand Prix` to the canonical `São Paulo Grand Prix` event when the schedule contains it.

Examples:

```bash
python main.py race 2026 "Brazilian Grand Prix"
python main.py sprint 2026 "Brazilian Grand Prix"
python main.py qualifying 2026 "Brazilian Grand Prix"
```

To score a session that already has complete results, append `--measure-variation`. The target event is held out of model training, predictions are generated from the remaining history, and actual-vs-predicted errors are written for evaluation:

```bash
python main.py race 2024 "São Paulo Grand Prix" --measure-variation
```

This option is a backtest, not a way to create a pre-event forecast. It fails when the requested event does not have complete winner, podium, and finishing-position labels or when excluding it leaves too little training data.

Each invocation collects the named event for every season from 2021 through the target year, refreshes the driver/constructor reference data, and rebuilds the feature table for the requested session. It then validates the feature table, trains the winner, podium, and finishing-position models, and predicts only the requested year and round. It does not silently substitute another round if the requested session is unavailable.

Race and sprint runs also print a 2,000-iteration `standard_dry` Monte Carlo simulation. Qualifying runs produce prediction output without a race simulation.

## Outputs

Generated files are stored under the project root:

| Location | Contents |
| --- | --- |
| `data/raw/` | Collected session data and yearly schedules, including `<year>_schedule.parquet`. |
| `data/processed/race_features.parquet` | Rebuilt race feature table. |
| `data/processed/sprint_features.parquet` | Rebuilt sprint feature table. |
| `data/processed/qualifying_features.parquet` | Rebuilt qualifying feature table. |
| `models/` | Trained winner, podium, and position model artifacts. |
| `data/predictions/predictions_<session>_<year>_rnd<round>.parquet` | Prediction rows for the requested session and event. |
| `data/predictions/simulation_<session>_<year>_rnd<round>.parquet` | Saved Monte Carlo results for race and sprint runs. |
| `data/predictions/variation_<session>_<year>_rnd<round>.parquet` | Backtest error summary, created only with `--measure-variation`. |

Prediction rows include session/year/round/event and generation-time metadata so the Streamlit dashboard can discover and select saved runs. Run `streamlit run app/app.py` to view the newest artifact by default, select other saved sessions, inspect saved simulations, or review variation metrics when available. With no saved prediction files, the dashboard clearly labels its built-in sample as a demo.

The terminal displays driver, constructor, win-probability, podium-probability, and expected-position columns. Race and sprint runs additionally display simulated win, podium, DNF, and position estimates. Predictions and simulations are model outputs, not guarantees of race results. Chronological grouped log-loss results automatically set the winner ensemble's model weights; if the backtest scores are missing or invalid, the pipeline retains the default 20/30/50 weights.

## Verify the environment

Run the test suite from the repository root:

```bash
python -m unittest discover tests
```

The command should finish with `OK`. If the project environment is not activated, use `.venv/bin/python -m unittest discover tests` instead.

## Troubleshooting

- **Year is rejected:** use a target year of 2021 or later.
- **No schedule is available:** confirm that the target season has a saved schedule in `data/raw/` and that the event name matches a scheduled event. Retry when the schedule can be retrieved.
- **No requested session data:** the session may not have occurred yet, may not be available from FastF1, or collection may have failed. Check network access and the target event/year; the pipeline intentionally errors rather than predicting a different round.
- **FastF1 collection errors or takes a long time:** check internet connectivity, allow the initial download to complete, and rerun. Collection failures are logged; the eventual missing schedule or session error identifies when required data was not obtained.
- **Parquet or dependency import errors:** activate the project virtual environment and reinstall `requirements.txt`; `pyarrow` is required for Parquet support.
- **Model training fails with too little usable history:** ensure that real result data exists for the requested event in enough seasons from 2021 through the target year. A current or future event may not yet have a completed qualifying, sprint, or race session.
