---
title: F1 Prediction Pipeline Runbook
description: Bounded event collection, consistent probabilistic forecasts, and chronological validation.
---

## Overview

The point-in-time pipeline collects at most six related weekends per season from 2021 through the target year, not every historical round. Training, prediction and walk-forward evaluation are separate commands.

## Prerequisites

- Python 3.11 or a compatible newer version.
- Network access to retrieve FastF1 schedules and selected sessions. Schedules contain the full calendar, but unselected session data is not downloaded. FastF1's cache and saved raw-data files are reused.
- Run commands from the project root, where `main.py` and `requirements.txt` are located.

Create an environment and install the project dependencies:

```powershell
python -m pip install uv
python -m uv venv --python 3.11 .venv
python -m uv pip install --python .venv\Scripts\python.exe -r requirements.txt pytest
.\.venv\Scripts\Activate.ps1
```

On subsequent Windows runs, activate the environment with `.\.venv\Scripts\Activate.ps1`, or call its Python executable directly.

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

Collection fills missing elapsed sessions only for selected weekends and fails clearly if required selected data remains absent. Feature files are content-addressed and support `pre_weekend`, `post_fp`, and `post_quali` stages.

Useful options are `--stage`, `--skip-collection`, `--model-path`, `--n-simulations`, `--seed`, `--scenario`, `--events-per-season`, `--similar-circuits`, and `--measure-variation`. Backtests use temporary artifacts and never modify `models/`.

## Bounded historical collection

The default per-season budget is six events, including the target-circuit event when it exists in that season. Selection prioritizes:

1. The target event, matched by event name or normalized circuit.
2. Its two preceding calendar events.
3. Up to two additional events from the target's similarity preset.
4. Additional preceding events, then remaining similar events, until the budget is reached.

Events are deduplicated. In the target season, rounds after the requested target are never selected. In older seasons, a similar circuit later in that season's calendar is eligible; chronological feature building still excludes its results from any earlier forecast.

Circuit families in `src/data/event_selection.py` are editable collection heuristics, not learned similarity scores. For example, the Interlagos family includes Austin, Spielberg, Barcelona, Silverstone and Suzuka. Unknown circuits produce an explicit warning and use the target plus preceding events. You can override or disable the presets:

```powershell
python main.py race 2026 "Brazilian Grand Prix" --events-per-season 6
python main.py race 2026 "Brazilian Grand Prix" --events-per-season 6 --similar-circuits Austin Silverstone
python main.py race 2026 "Brazilian Grand Prix" --events-per-season 4 --similar-circuits
```

The collector logs the exact selected events before loading their missing sessions. Repeating the command does not reload already saved sessions. `--skip-collection` uses existing data without network collection. Previously cached events are not deleted and can still supply historical evidence.

> [!IMPORTANT]
> A smaller download budget means less evidence. Season points and form summarize the collected sessions, not necessarily complete championship standings. Points-per-start and history-count features help describe this coverage, but do not recover missing results. Related-circuit backtests also do not establish accuracy across the entire F1 calendar.

## Prediction consistency and assumptions

The v2 model trains competitive strength on non-retired drivers rather than penalizing retirements in both ranking and simulation. Ranking and logistic components use a 30-event recency half-life; examples from earlier regulation eras receive one-quarter weight. Recent form is era-specific, and sprint/Grand Prix histories and qualifying formats are separate.

Qualifying gaps compare the same Q1, Q2 or Q3 phase. Practice pace excludes deleted, inaccurate, flagged and pit laps when those flags are available. Long-run residuals compare multiple drivers within the same session, compound, tyre age and ten-minute track-time cohort when timestamps exist. Sample count, dispersion and timestamp coverage expose weak comparisons. Fuel load is unobserved, so these remain imperfect pace estimates.

Retirement probabilities are shrunk by entity and recency. Mechanical, collision and unidentified terminal causes are exclusive components; unknown causes are not invented. A shrunk Gaussian-copula race effect allows common retirement shocks while preserving each driver's marginal risk. Its moment estimate is approximate, not a fully fitted accident or team-reliability model.

One temperature is fitted against final win and podium probabilities from expanding, past-only out-of-fold predictions, including the same retirement and reference-scenario process used at inference. There is no second temperature that overwrites winner calibration. Baselines use the same calibration races and simulator.

All position probabilities, win/podium/top-10 probabilities and expected positions come from one permutation distribution. A half-simulation uniform-order prior within sampled finisher/retiree groups reduces rare-order zeros while preserving position-probability row and column sums and impossible-outcome constraints. Saved outputs include the original strength, base retirement risks, dependence, temperature, seed and simulation count so the dashboard can replay the forecast without treating final win probabilities as new strengths.

Qualifying predictions disable race retirement and safety-car processes. Non-reference scenarios are labeled heuristic sensitivity analyses. Their rain, safety-car and degradation multipliers are not learned driver-specific effects and are not separately calibrated.

Remaining assumptions include Plackett-Luce relative odds, a constant-hazard retirement-distance proxy, finishers preceding retirees, and no lap-level fuel, strategy or lapped-car classification model. This repair does not introduce a Bayesian driver/car decomposition or claim that driver skill is causally identified.

## Validation and uncertainty

Walk-forward evaluation starts after eight completed training events, refits every four events, and honors the supplied seed and simulation count. At least nine completed events are required to produce a report. Fewer than ten held-out or calibration races generate explicit warnings.

Reports include winner/podium/top-10/DNF Brier scores, winner and podium log loss, marginal finishing-position log loss, ranked probability score, rank correlations, position errors and P10-P90 outcome-range coverage. `finisher_pl_nll_diagnostic` is a conditional, no-scenario PL diagnostic, not the likelihood of the full simulation mixture. It is not used as a headline model-comparison score. Overall signed position bias is omitted because it is zero by construction for a complete field; driver/team signed-error reports remain available.

The confidence report uses 500 circular race-block bootstrap samples, including paired differences versus the grid baseline. Blocks have length `ceil(number_of_races ** (1/3))`. Intervals are omitted with fewer than five held-out races. These intervals describe the sampled backtest, not a guarantee of improvement or a remedy for selective circuit coverage.

P10-P90 position ranges describe conditional simulated outcomes, not posterior parameter confidence. `win_probability_mcse` describes approximate numerical Monte Carlo error only. More simulations reduce sampling noise, not model misspecification. Calibration must still be checked on later held-out races before trusting the forecasts.

Old model artifacts fail the data/code fingerprint check and must be retrained. Legacy/demo predictions remain viewable, but scenario replay is disabled until forecasts contain the current latent-input fields.

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
| `reports/walk_forward_<session>_<year>_rnd<round>_<stage>_confidence.parquet` | Race-block intervals and paired differences versus the grid baseline. |

Prediction rows include session/year/round/event and generation-time metadata so the Streamlit dashboard can discover and select saved runs. Run `streamlit run app/app.py` to view the newest artifact by default, select other saved sessions, inspect saved simulations, or review variation metrics when available. With no saved prediction files, the dashboard clearly labels its built-in sample as a demo.

The terminal keeps a dedicated `RACE PREDICTIONS` table containing forecast fields only. Predictions include DNF risk, median and P10–P90 position ranges, full `prob_p*` distributions, stage, model version, training range, scenario, simulation count, and grid-order disagreement. Actual outcomes and error metrics are printed and persisted separately.

## Verify the environment

Run the test suite from the repository root:

```powershell
$env:PYTHONUTF8 = "1"
.\.venv\Scripts\python.exe -m pytest -q
```

The suite runs offline with mocked collection. `python -m unittest discover tests` remains supported. UTF-8 mode avoids Windows locale-dependent decoding of existing Unicode UI sources.

## Troubleshooting

- **Year is rejected:** use a target year of 2021 or later.
- **No schedule is available:** confirm that the target season has a saved schedule in `data/raw/` and that the event name matches a scheduled event. Retry when the schedule can be retrieved.
- **No requested session data:** the session may not have occurred yet, may not be available from FastF1, or collection may have failed. Check network access and the target event/year; the pipeline intentionally errors rather than predicting a different round.
- **FastF1 collection errors or takes a long time:** check internet connectivity, allow the initial download to complete, and rerun. Collection failures are logged; the eventual missing schedule or session error identifies when required data was not obtained.
- **Parquet or dependency import errors:** activate the project virtual environment and reinstall `requirements.txt`; `pyarrow` is required for Parquet support.
- **Model training fails with too little usable history:** ensure that real result data exists for the requested event in enough seasons from 2021 through the target year. A current or future event may not yet have a completed qualifying, sprint, or race session.
