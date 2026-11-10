# F1 Prediction Pipeline Runbook

This guide covers the point-in-time F1 pipeline. Collection is incremental across every round from 2021 through the target event; training, prediction and walk-forward evaluation are separate commands.

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

The legacy automatic command remains available, while explicit commands avoid retraining during prediction.

```text
python main.py <race|sprint|qualifying> <year> "<event name>"
python main.py train <race|sprint|qualifying> <year> "<event name>"
python main.py predict <race|sprint|qualifying> <year> "<event name>"
python main.py evaluate <race|sprint|qualifying> <year> "<event name>"
```

The target year must be 2021 or later. For the Brazilian Grand Prix, use the event name recognized by the season schedule; the pipeline supports resolving `Brazilian Grand Prix` to the canonical `São Paulo Grand Prix` event when the schedule contains it.

Examples:

```bash
python main.py race 2026 "Brazilian Grand Prix"
python main.py sprint 2026 "Brazilian Grand Prix"
python main.py qualifying 2026 "Brazilian Grand Prix"
python main.py train race 2026 "Brazilian Grand Prix" --stage post_quali
python main.py predict race 2026 "Brazilian Grand Prix" --stage post_quali --skip-collection
python main.py evaluate race 2026 "Brazilian Grand Prix" --stage post_quali
```

To score a session that already has complete results, append `--measure-variation`. The target event is held out of model training, predictions are generated from the remaining history, and actual-vs-predicted errors are written for evaluation:

```bash
python main.py race 2024 "São Paulo Grand Prix" --measure-variation
```

This option is a backtest, not a way to create a pre-event forecast. It fails when the requested event does not have complete winner, podium, and finishing-position labels or when excluding it leaves too little training data.

Collection fills every missing elapsed session in each calendar from 2021 through the target round and fails clearly if required data remains absent. Feature files are content-addressed and support `pre_weekend`, `post_fp`, and `post_quali` stages. The single race model blends ranking strengths, predicts driver/team reliability, and samples coherent finishing orders; win, podium, top-10 and position outputs therefore come from one distribution.

Useful options are `--stage`, `--skip-collection`, `--model-path`, `--n-simulations`, `--seed`, `--scenario`, and `--measure-variation`. Backtests use temporary artifacts and never modify `models/`.

## Outputs

Generated files are stored under the project root:

| Location | Contents |
| --- | --- |
| `data/raw/` | Collected session data and yearly schedules, including `<year>_schedule.parquet`. |
| `data/processed/race_features.parquet` | Rebuilt race feature table. |
| `data/processed/sprint_features.parquet` | Rebuilt sprint feature table. |
| `data/processed/qualifying_features.parquet` | Rebuilt qualifying feature table. |
| `models/<run_id>/` | Versioned race-model artifact, training feature snapshot and provenance metadata. |
| `data/predictions/predictions_<session>_<year>_rnd<round>.parquet` | Prediction rows for the requested session and event. |
| `data/predictions/variation_<session>_<year>_rnd<round>.parquet` | Backtest error summary, created only with `--measure-variation`. |
| `reports/walk_forward_<session>_<year>_rnd<round>_<stage>_*.parquet` | Race-wise metrics, baselines, reliability tables, predictions and bias reports. |

Prediction rows include session/year/round/event and generation-time metadata so the Streamlit dashboard can discover and select saved runs. Run `streamlit run app/app.py` to view the newest artifact by default, select other saved sessions, inspect saved simulations, or review variation metrics when available. With no saved prediction files, the dashboard clearly labels its built-in sample as a demo.

The terminal keeps a dedicated `RACE PREDICTIONS` table containing forecast fields only. Predictions include DNF risk, median and P10–P90 position ranges, full `prob_p*` distributions, stage, model version, training range, scenario, simulation count, and grid-order disagreement. Actual outcomes and error metrics are printed and persisted separately.

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
