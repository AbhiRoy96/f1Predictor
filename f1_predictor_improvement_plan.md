# F1 Predictor: Improvement Plan

Based on the São Paulo GP 2026 backtest output (`--measure-variation`) and a read-through of the code in `f1Predictor-predictor-dev.zip`.

**What I reviewed:** `main.py`, `src/features/engineer_features.py`, `src/models/*` (train_winner, train_podium, train_position, calibrate, evaluate, baselines), `src/simulation/*`, `src/data/normalise_entities.py`, `src/data/collect_season.py`, `src/data/validate_data.py`, `src/utils/config.py`, `README_RUNBOOK.md`, and the Race Prediction Streamlit page.
**What I did not review:** your local `data/` folder (not in the zip), the test files in detail, and the other Streamlit pages.

**Corrections to my earlier message**
- I suggested the `prediction_generated_at_utc` timestamp might indicate leakage. It doesn't. It is only run metadata. Your features are cut off per session by `SessionStartUtc < start` in `build_features`, which is the right approach. Leakage in the backtest comes from somewhere else (see P1-3).
- I said there is "no DNF modelling". There is a flat DNF rate (5%) in the simulator, applied to every driver equally. There is no per-driver or per-team DNF model.
- For `CAM`, I guessed a name earlier. I can't verify it from the code. The fix below reads names from your own data instead of a hand-written dictionary.

---

## 0. Summary

Your pipeline is well structured: point-in-time features, a data-contract validator, a chronological backtest, baselines and a simulator. The issues are in how the pieces connect.

| # | Issue | Where | Effect on output |
|---|---|---|---|
| P0-1 | Each run collects only the **named event** per season, not whole seasons | `main.py: collect_historical_data` | Training data may be ~1 race per year |
| P0-2 | `fillna(0)` at predict time vs median imputation at train time, and `0` means "better than P1" for position features | `main.py`, `evaluate.py` | Train/serve skew, rookies and new teams look elite |
| P0-3 | Laps, weather and race-control data are collected but never used; same-weekend grid and qualifying result are not features | `main.py: ensure_processed_data`, `config.py: NON_FEATURE_COLUMNS` | Model can't see the strongest single predictor of a race result |
| P0-4 | Temperature calibration is fitted on the same data the model trained on | `calibrate.py`, `train_winner.py` | Overconfidence (Norris 0.91) is not corrected |
| P0-5 | Win, podium and position come from three unrelated models | `main.py` | P(win) 0.91 > P(podium) 0.80; podium sum 2.85 |
| P0-6 | Simulator turns win probability into pace with `-10·ln(p)` and a noise std of 2 | `race_simulator.py` | Simulation is almost deterministic and adds no independent information |
| P0-7 | Backtest runs overwrite `models/*.joblib` | `main.py: _train_models` | Production models silently replaced by held-out models |
| P0-8 | Hard-coded fake feature importances in the UI | `01_Race_Prediction.py` | The "Model Explainability" chart is not from the model |
| P1 | Driver/team naming and team lineage (LIN, CAM, Audi, Cadillac, RB vs Racing Bulls) | `normalise_entities.py`, `config.py` | Missing names, split team histories |
| P1 | Backtest inconsistencies, no podium/position backtest, in-sample metrics only | `evaluate.py`, `train_podium.py`, `train_position.py` | You can't tell whether the model beats a baseline |

---

## 1. P0: Bugs and structural problems to fix first

### P0-1. Confirm your training data covers whole seasons

`README_RUNBOOK.md` says: "Each invocation collects the named event for every season from 2021 through the target year." `collect_historical_data` loops over years and calls `collect_event(year, event)` for the one event only. Unless you ran `collect_season` separately, `data/raw/` holds one Grand Prix per season, and every "form", "season points" and "career" feature is built from those races only.

**Check (run from project root):**

```python
from src.features.engineer_features import load_results
r = load_results()
print(r[r.SessionType == "R"].groupby("Year").RoundNumber.nunique())
```

If you see numbers like 1, 1, 1, 1, 1 (and 19 or so for 2026), this is your biggest problem. If you see about 22-24 per year, the data is fine and you can skip to P0-2.

**Fix:** collect every round up to the target round, incrementally.

```python
# main.py
import fastf1

def collect_history(year: int, round_number: int) -> None:
    for y in range(HISTORICAL_START_YEAR, year + 1):
        schedule = fastf1.get_event_schedule(y, include_testing=False)
        for _, ev in schedule.iterrows():
            if y == year and ev["RoundNumber"] > round_number:
                break
            if _already_collected(y, int(ev["RoundNumber"])):   # check data/raw/{y}_r_results.parquet keys
                continue
            collect_event(y, ev["EventName"], event=ev, raise_on_failure=False)
    collect_driver_constructor_stats()
```

`collect_event` already persists with key columns, so re-runs are cheap once `_already_collected` exists.

**Acceptance:** `groupby("Year").RoundNumber.nunique()` returns the full calendar for each completed season and rounds `<=` target for the target season.

### P0-2. Remove `fillna(0)` and handle missing values deliberately

Your pipelines already include `SimpleImputer(strategy="median")`, but `main.py` calls `target_event_df[feat_cols].fillna(0)` before predicting, and `run_chronological_backtest` does `.fillna(0)` on both train and test. Problems:

1. **Train/serve skew.** Training (in `train_*`) uses median imputation. Prediction uses 0. Same feature, different meaning.
2. **0 is the best possible value for position features.** `driver_race_career_avg_position`, `form_3`, `form_5`, `*_season_avg_position` are NaN for rookies and new entrants (this is likely why `LIN`, `CAM`, Audi and Cadillac drivers get odd values). Filling with 0 says "averages P0".
3. **All-NaN columns.** The `prior_weekend_*` features are all NaN in your pipeline (P0-3). `SimpleImputer` silently drops columns that are entirely NaN at fit time, so the model's real feature set is smaller than `feature_columns` suggests.

**Fix:**

```python
# main.py: let the pipeline own imputation
X_pred = target_event_df[feat_cols]          # no fillna

# evaluate.py: same in run_chronological_backtest
X_train = train_df[feature_cols]
X_test  = test_df[feature_cols]
```

Then, in `engineer_features.py`, make "no history" explicit:

- Add `*_is_missing` flags (or use `SimpleImputer(add_indicator=True)`).
- Fill position-type features with a neutral value such as the field median (about 10-11) or the team-mate's value, not 0.
- Use `SimpleImputer(strategy="constant", fill_value=0, keep_empty_features=True)` only for count-type features (`career_starts`, `career_wins`), where 0 is correct.

**Acceptance:** a test that builds features for a rookie and asserts `predict` input contains no value of 0 for any `*_avg_position`, `form_*` column.

### P0-3. Feed the model the information that actually decides races

Two separate gaps:

**(a) Weekend data is collected but not used.** `ensure_processed_data` calls `build_features(historical_results)` with results only. `laps`, `weather` and `messages` are never passed, so every `prior_weekend_*` feature is NaN. Your `engineer_features.main()` function does load them. Use the same loaders in `ensure_processed_data`:

```python
features = build_features(
    historical_results,
    laps=_read_parquets("*_laps.parquet"),
    weather=_read_parquets("*_weather.parquet"),
    messages=_read_parquets("*_messages.parquet"),
    news=_load_news(),
)
```

Also note `build_features` loops over every target row and re-filters the full results frame each time, so it is O(n²). Once you load whole seasons it will get slow. Pre-sort and use grouped cumulative operations or `groupby().expanding()`/`shift()` instead.

**(b) Grid and qualifying result are excluded.** `NON_FEATURE_COLUMNS` includes `GridPosition`, and no feature is built from this weekend's qualifying result. Qualifying history is included only through trailing averages (`form_3`, `form_5`), where the latest session is one of 3-5 and is diluted. For race winners, grid position is typically the strongest single predictor.

`GridPosition` is known before the race starts, so it is legitimate for a pre-race model. It was probably excluded to avoid leakage, but that was too broad.

Add these features for each driver, computed only from sessions with `SessionStartUtc` earlier than the target race:

| Feature | Definition |
|---|---|
| `grid_position` | Official grid after penalties (use the pre-race value) |
| `quali_position` | Position in this weekend's Q |
| `quali_gap_to_pole_pct` | `(best_lap / pole_lap − 1) × 100` |
| `quali_gap_to_teammate_pct` | Same, relative to the team-mate |
| `grid_penalty_places` | `quali_position − grid_position` |
| `pit_lane_start` | Boolean |
| `fp2_long_run_pace_rank` | Rank of median lap time on 5+ lap stints (tyre-corrected) |
| `fp_best_lap_gap_pct` | Gap to the fastest FP lap |

Keep **stages** separate so you can see how much each one adds:

- `pre_weekend` (history only)
- `post_fp` (adds practice)
- `post_quali` (adds qualifying and grid)

Store `stage` in the predictions file.

**Acceptance:** `race_features.parquet` contains the new columns, none of the `prior_weekend_*` columns is 100% NaN, and `validate_features_df` fails if a stage-specific column is populated from a later session than the stage allows.

### P0-4. Calibrate on out-of-sample scores

In `CalibratedRacePredictor.fit`, the base estimator is trained on `(X, y)` and then the temperature is optimised on `self._get_raw_scores(X)`, which are in-sample predictions of the same data. A gradient-boosted model with 100 trees fits its training races closely, so in-sample the temperature has nothing to correct and can even sharpen the distribution. This is one likely cause of Norris at 0.91.

On top of that, `run_chronological_backtest` calls `model.fit(X_train, y_train)` and `predict_proba(X_test)` without `groups`. In `fit` the temperature is then not learned (stays 1.0). In `predict_proba`, `groups` falls back to one group `"race_0"` for the entire test season because `Year`/`RoundNumber` are not in `feature_cols`. The backtest therefore evaluates a different model from the one you ship, and the Brier score is computed on season-wide softmax values.

**Fix:**

1. Generate out-of-fold scores with walk-forward splits by race (see section 4).
2. Fit the temperature on those out-of-fold scores only:

```python
import numpy as np
from scipy.optimize import minimize_scalar

def fit_temperature(scores_by_race, winner_idx_by_race):
    """scores_by_race: list of 1-D arrays of raw model scores (higher = better);
       winner_idx_by_race: list of ints, index of the actual winner in each array."""
    def nll(T):
        total = 0.0
        for s, w in zip(scores_by_race, winner_idx_by_race):
            z = s / T
            z = z - z.max()
            total -= z[w] - np.log(np.exp(z).sum())
        return total / len(scores_by_race)
    return minimize_scalar(nll, bounds=(0.3, 5.0), method="bounded").x
```

3. Pass `groups` in the backtest the same way production does, so validation and production behave the same.

**Acceptance:** a reliability table (section 4) for win and podium probabilities on out-of-fold predictions, and a stored `temperature` in the model artifact.

### P0-5. Replace three independent models with one race model and a simulation

Right now:

- Winner: calibrated ensemble with per-race softmax, so it sums to 1.
- Podium: separate XGBoost classifier, no per-race constraint (sum 2.85, should be 3).
- Position: separate XGBoost regressor trained with squared error, so it pulls everything toward the field mean.

Nothing connects them. That is why Verstappen has an expected position of 10.4 and a 40% podium chance (bimodal outcome: P2 or a bad day, but the regressor reports the mean), and why Norris has P(win) 0.91 > P(podium) 0.80.

**Target design:**

1. One model produces a **strength score** per driver per race (a ranking model).
2. A **DNF model** produces a retirement probability per driver.
3. A **Plackett-Luce simulation** samples full finishing orders from those two things.
4. Win, podium, top-10, expected position and the full position distribution are all computed from the same samples. They are then consistent by construction: win probabilities sum to 1, podium probabilities sum to 3, and P(win) ≤ P(podium) ≤ P(top 10).

**Strength model.** XGBoost has a ranking objective that is a direct fit for "one race = one group":

```python
from xgboost import XGBRanker

def make_relevance(df):
    # higher = better. DNFs get 0, classified finishers get n - position + 1
    n = df.groupby(["Year", "RoundNumber"])["Abbreviation"].transform("count")
    rel = (n - df["target_position"] + 1).clip(lower=0)
    return rel.where(~df["target_dnf"].astype(bool), 0).astype(int)

df = df.sort_values(["Year", "RoundNumber"])          # rows of a group must be contiguous
qid = df["Year"].astype(str) + "_" + df["RoundNumber"].astype(str)

ranker = XGBRanker(
    objective="rank:pairwise",
    n_estimators=300, max_depth=3, learning_rate=0.05,
    subsample=0.8, colsample_bytree=0.8,
    min_child_weight=5, reg_lambda=5.0, random_state=42,
)
ranker.fit(X_train, make_relevance(df_train), qid=qid_train.factorize()[0])
strength = ranker.predict(X_race)       # one score per driver
```

Keep your logistic regression as a second, simpler strength model (a conditional logit with 6-8 features) and blend the scores. With roughly 130 races × 20 drivers, a small, regularised model is safer than a deep one.

**Simulation (Plackett-Luce via the Gumbel trick):**

```python
import numpy as np

def simulate_race(strength, p_dnf, n_sims=20_000, pl_temperature=1.0,
                  team_idx=None, team_sigma=0.0, seed=42):
    """strength: higher = faster. p_dnf: per-driver retirement probability.
    team_idx/team_sigma: optional shared per-team shock per simulated race
    (correlates team-mates; models strategy, setup and reliability swings)."""
    rng = np.random.default_rng(seed)
    n = len(strength)
    util = strength / pl_temperature + rng.gumbel(size=(n_sims, n))

    if team_idx is not None and team_sigma > 0:
        n_teams = int(np.max(team_idx)) + 1
        util += rng.normal(0, team_sigma, size=(n_sims, n_teams))[:, team_idx]

    retired = rng.random((n_sims, n)) < p_dnf
    util = np.where(retired, -1e6 + rng.random((n_sims, n)), util)   # retirees go to the back

    order = np.argsort(-util, axis=1)                    # order[s, k] = driver in place k+1
    pos = np.empty_like(order)
    places = np.broadcast_to(np.arange(1, n + 1), (n_sims, n))
    np.put_along_axis(pos, order, places, axis=1)        # pos[s, driver] = finishing place
    return pos, retired

pos, retired = simulate_race(strength, p_dnf, team_idx=team_idx, team_sigma=0.3)
out = pd.DataFrame({
    "win_probability":    (pos == 1).mean(0),
    "podium_probability": (pos <= 3).mean(0),
    "top10_probability":  (pos <= 10).mean(0),
    "expected_position":  pos.mean(0),                   # includes DNFs at the back
    "dnf_probability":    retired.mean(0),
    "median_position":    np.median(pos, axis=0),
    "p10_position":       np.percentile(pos, 10, axis=0),
    "p90_position":       np.percentile(pos, 90, axis=0),
})
assert abs(out.win_probability.sum() - 1) < 1e-9
assert abs(out.podium_probability.sum() - 3) < 1e-9
```

Tune `pl_temperature` by maximising the Plackett-Luce log-likelihood of the **observed** finishing orders on out-of-fold data:

```python
def pl_nll(scores, finish_order, T):
    """scores: strengths per driver. finish_order: driver indices in observed finishing order
    (use classified finishers only, or the top 10)."""
    s = scores[finish_order] / T
    rev_lse = np.logaddexp.accumulate(s[::-1])[::-1]     # log-sum-exp of s[k:]
    return -(s - rev_lse).sum()
```

Report the median and a P10-P90 range of position next to the mean. That fixes the "Verstappen expected P10.4" confusion because the app now shows the spread, not just a misleading average.

**Acceptance:**

- win probabilities sum to 1 and podium probabilities to 3 (to 1e-9) for every saved prediction;
- P(win) ≤ P(podium) ≤ P(top 10) for every driver;
- `train_podium.py` and `train_position.py` are removed or kept only as baselines in the evaluation.

### P0-6. Fix or remove the current Monte Carlo simulator

`MonteCarloRaceSimulator.simulate` does this:

```python
base_pace = -10.0 * np.log(np.clip(win_probabilities, 1e-4, 1.0))
total_time = base_pace + np.random.normal(0, 2.0 * pace_variance_scale)
```

With your output: Norris ≈ 0.95, Verstappen ≈ 28, Russell ≈ 38, and every driver at p ≤ 0.0001 is clipped to 92.1. The noise std is 2.0 (and a safety car only halves gaps). The simulated order is therefore effectively fixed by sorting the model's own win probabilities. Norris will win about 100% of simulations even though the model says 0.91, and all drivers below roughly P3 are in a random order among tied values. The result looks like an independent uncertainty estimate, but is not.

Other issues in the same file:

- `np.random.seed(...)` seeds the global RNG. Use `np.random.default_rng(seed)`.
- DNF drivers are removed from `finish_positions`, so `expected_position` is the mean position **conditional on finishing**. It will understate how bad a backmarker's day can be.
- The same `dnf_base_rate` is applied to all drivers.
- `scenarios.py` defines `vsc_probability`, `overtaking_difficulty`, `tyre_deg_multiplier` and `weather_rain_probability`, but none are used in `simulate`.
- The loop is pure Python (`for sim in range(...)`, `for i in range(n_drivers)`). The vectorised version in P0-5 runs 20,000 sims in milliseconds.

**Fix:** replace it with `simulate_race` from P0-5, and turn scenarios into parameters of that function (e.g. `pl_temperature` ↑ and `p_dnf` ↑ for `wet_chaos`; an SC probability that adds a re-shuffle step).

### P0-7. Stop backtests from overwriting production models

`_train_models` calls `train_and_save_winner_models(training_path)` etc. without `output_dir`, so they always write to `models/`. Running `--measure-variation` therefore replaces `models/winner_ensemble.joblib` with a model trained without the held-out race. Every normal run also retrains and overwrites.

**Fix:**

```python
def _train_models(feature_path, training_features=None):
    if training_features is None:
        return (train_and_save_winner_models(feature_path),
                train_and_save_podium_model(feature_path),
                train_and_save_position_model(feature_path))
    with tempfile.TemporaryDirectory(prefix="backtest_", dir=PROCESSED_DATA_DIR) as d:
        d = Path(d)
        training_path = d / feature_path.name
        training_features.to_parquet(training_path, index=False)
        return (train_and_save_winner_models(training_path, output_dir=d),
                train_and_save_podium_model(training_path, output_dir=d),
                train_and_save_position_model(training_path, output_dir=d))
```

Longer term, save artifacts to `models/{run_id}/` with a `metadata.json` (git SHA, data range, feature list, row count, seed, metrics), and make "train" and "predict" separate commands. `mlflow` and `shap` are in `requirements.txt` but I found no usage; either wire them in or remove them.

### P0-8. Replace the hard-coded feature importances in the UI

`app/pages/01_Race_Prediction.py` uses a fixed list of six feature names with fixed importances `[0.34, 0.24, 0.18, 0.11, 0.08, 0.05]` for the "Model Explainability" chart. These are not from the model, and they would be shown for every race. Either:

- compute real importances (XGBoost `feature_importances_` with gain, or SHAP values for the specific race, per driver), save them next to the predictions file, and load them in the page; or
- remove the section until it is real.

The built-in "demo" data (VER 0.42 win, etc.) is labelled as a demo, which is fine. The importance chart is not labelled.

---

## 2. P1: Data quality and entity handling

### P1-1. Driver names

`normalise_driver_name` uses a hand-written dictionary and falls back to the abbreviation, which is why `LIN` and `CAM` appear as codes. Don't keep maintaining that list. FastF1's results already contain full names. Build the lookup from your data and use the dictionary only as a fallback:

```python
def build_driver_lookup(results: pd.DataFrame) -> dict[str, str]:
    cols = [c for c in ("FullName", "BroadcastName") if c in results.columns]
    latest = results.sort_values("SessionStartUtc").drop_duplicates("Abbreviation", keep="last")
    name_col = cols[0] if cols else None
    return dict(zip(latest["Abbreviation"], latest[name_col])) if name_col else {}
```

Add a test that fails when any driver in the latest race has `DriverName == Abbreviation`.

### P1-2. Team names and team lineage

- `normalise_constructor` exists, but `_normalize_results` never calls it. Your output has `Haas F1 Team` although the alias table maps `haas f1 team` to `Haas`.
- `CONSTRUCTOR_ALIASES` has no entries for **Audi**, **Cadillac** or **Racing Bulls**, and still maps `rb` to `RB`. `CONSTRUCTOR_COLORS` in `config.py` also lacks them. Teams with inconsistent names have their histories split across names.
- Audi is the former Sauber operation. If you key team history on `TeamName`, Audi starts with zero history, while the cars are a continuation. Use a lineage id for history features and keep the display name separate:

```python
TEAM_LINEAGE = {
    "Kick Sauber": "sauber_lineage", "Alfa Romeo": "sauber_lineage", "Sauber": "sauber_lineage", "Audi": "sauber_lineage",
    "RB": "rb_lineage", "AlphaTauri": "rb_lineage", "Toro Rosso": "rb_lineage", "Racing Bulls": "rb_lineage",
    "Aston Martin": "aston_lineage", "Racing Point": "aston_lineage", "Force India": "aston_lineage",
    "Alpine": "alpine_lineage", "Renault": "alpine_lineage",
    "Haas": "haas", "Cadillac": "cadillac",       # Cadillac is a genuinely new entry: no history
    # ...
}
```

Apply `normalise_constructor` in `_normalize_results`, compute team features by `team_lineage`, and add `team_is_new_entry` as a feature.

### P1-3. Regulation change in 2026

A large part of your history (2021-2025) was built under different regulations. Team strength carries over only partly. Don't treat it as 2026 data:

- Add `regulation_era` and `rounds_into_era`.
- Use exponentially weighted form (half-life 4-6 races) instead of equal-weight `form_5`.
- For team and driver strength, use a hierarchical or shrinkage estimate: `strength_2026 = w · current_season_estimate + (1 − w) · prior`, where `w` grows with the number of 2026 races (`w = n / (n + k)`, `k` around 4-6).
- Keep `season_*` features, but give them more weight than `career_*` for 2026.

### P1-4. Backtest leakage in `--measure-variation`

`_prepare_backtest_data` removes only the held-out race from training. Training still includes **later** races of the same or later seasons if you backtest a past event (the São Paulo 2026 run is not affected because it is nearly the last round, but a 2024 run is). That makes backtest numbers optimistic.

**Fix:** train on rows with `SessionStartUtc < held_out.SessionStartUtc` only.

```python
cutoff = held_out["SessionStartUtc"].min()
training = features.loc[features["SessionStartUtc"] < cutoff].copy()
```

Also assert in a test that features for a race row are identical whether built from full data or data truncated at that session's start (point-in-time test, section 6).

---

## 3. P2: Modelling improvements (after P0 is done)

1. **Reliability model (DNF).** Build `target_dnf` from the `Status` / `ClassifiedPosition` columns FastF1 provides (I didn't see them used in the feature code, so confirm they survive into `data/raw/*_results.parquet`). Features: trailing team DNF rate, driver incident rate, grid position (front vs midfield), circuit safety-car rate, wet-race flag. Use shrinkage because DNFs are sparse:

   ```python
   def shrunk_rate(k, n, prior_rate, prior_strength=20):
       return (k + prior_rate * prior_strength) / (n + prior_strength)
   ```

   Separate mechanical failures from collisions: they have different drivers (the first is team-level, the second is driver and grid-position-level).

2. **Position target.** `Position` for retired cars is a classified place, so DNFs add large positive errors to the regression target (Bottas 22, Pérez 18, Stroll 21, CAM 20 in your output). Another reason to drop the regressor in favour of strength + DNF.

3. **Team-mate decomposition.** Model `strength = team_strength + driver_skill`, estimated from within-team comparisons (qualifying gap to team-mate, race pace gap). This helps when teammates differ a lot (Antonelli podium 0.73 vs Russell 0.09 in the same Mercedes looks suspicious and should be checked against their qualifying and pace features).

4. **Circuit features.** Track type (street/permanent, high or low downforce), overtaking difficulty (historical position change from grid to finish), safety-car rate per circuit, tyre degradation, altitude (Interlagos), and typical rain probability. Use these as inputs to `p_dnf`, `pl_temperature` (processional circuits get a lower temperature, so grid matters more) and a safety-car shuffle step.

5. **Regularisation and feature count.** Currently about 80+ features for roughly 2-3k rows (when whole seasons are loaded). Start with a lean core set, then add groups only if they improve out-of-fold log loss:

   - `grid_position`, `quali_gap_to_pole_pct`, `quali_gap_to_teammate_pct`
   - `team_ewma_points` (last 5-6 races), `team_ewma_quali_gap`
   - `driver_ewma_finish_vs_grid` (race craft)
   - `circuit_overtaking_difficulty`, `team_dnf_rate_shrunk`
   - `fp2_long_run_pace_rank` (if available)

   Use monotonic constraints where the relationship is obvious (worse grid → worse strength).

6. **Ensemble.** The current inverse-log-loss weights come from a backtest that uses `fillna(0)` and season-wide softmax (P0-2, P0-4). Recompute them after fixing those, using out-of-fold scores. Average **scores** (or logits) across models rather than probabilities, then temper once at the end.

---

## 4. Evaluation: how to know it's better

One race isn't evidence. In the São Paulo run, Norris (the model's top pick) won, so top-1 accuracy looks fine, but the probabilities were poorly structured. Judge on many races.

### 4.1 Walk-forward backtest by race

```python
def walk_forward(df, fit, predict, min_train_races=30, refit_every=4):
    races = (df[["Year", "RoundNumber", "SessionStartUtc"]]
             .drop_duplicates(["Year", "RoundNumber"]).sort_values("SessionStartUtc"))
    out, model = [], None
    for i, (y, r, start) in enumerate(races.itertuples(index=False)):
        if i < min_train_races:
            continue
        if model is None or (i - min_train_races) % refit_every == 0:
            model = fit(df[df["SessionStartUtc"] < start])
        test = df[(df["Year"] == y) & (df["RoundNumber"] == r)]
        out.append(predict(model, test).assign(Year=y, RoundNumber=r))
    return pd.concat(out, ignore_index=True)
```

Replace the current by-season split in `run_chronological_backtest` (it needs at least 3 seasons, and a season is a coarse unit).

### 4.2 Metrics to report per run

| Target | Metrics |
|---|---|
| Win | Per-race log loss on the winner, Brier score, top-1 and top-3 hit rate |
| Podium | Brier score, log loss, **set overlap** (how many of the predicted top 3 are in the real top 3), calibration by bin |
| Full order | Mean Spearman and Kendall tau per race, Plackett-Luce log-likelihood |
| Position | MAE vs baseline, median absolute error, **signed** error (bias) |
| DNF | Brier score, calibration |

### 4.3 Always compare with baselines

You already have `PolePositionBaseline`, season points and form baselines in `baselines.py`. Use them in the same walk-forward loop and print a single table:

| Model | Win log loss | Win Brier | Podium Brier | Top-3 overlap | Spearman |
|---|---|---|---|---|---|
| Grid order (softmax, T fitted) | | | | | |
| Championship standings | | | | | |
| Team rating only | | | | | |
| **Your model** | | | | | |

If the model does not beat "grid order" at the post-quali stage, it is not adding value there. Note that `PolePositionBaseline` uses `driver_qualifying_season_avg_position` by default, which is *season average*, not the actual grid. After P0-3 point it at `grid_position`.

### 4.4 Reliability (calibration) tables

```python
def reliability(p, y, bins=10):
    b = pd.cut(p, np.linspace(0, 1, bins + 1), include_lowest=True)
    return (pd.DataFrame({"p": p, "y": y})
              .groupby(b, observed=True)
              .agg(n=("y", "size"), mean_p=("p", "mean"), hit_rate=("y", "mean")))
```

Check that `hit_rate ≈ mean_p` in every bin. Pay attention to the top bin: if drivers assigned 0.8-1.0 win only about 0.5 of the time, you are overconfident. Win probabilities are sparse (roughly 20 per race, 1 winner), so use podium and top-10 calibration as well, where there is more data.

### 4.5 Log signed errors

`_measure_prediction_variation` stores both signed and absolute errors, which is good. Add rolling means of the signed error per team and per driver, so systematic bias (e.g. consistently over-rating a team) is visible.

### 4.6 Record the stage

Add `stage` (`pre_weekend`, `post_fp`, `post_quali`) and `model_version` to every predictions file, so you can compare how much each stage improves accuracy.

---

## 5. P3: App, UX and engineering

1. **Show uncertainty.** In the Race Prediction page show P10-P90 position range, DNF probability and a stacked position-distribution chart (`prob_p1 ... prob_p10`) per driver.
2. **Show provenance.** Display `stage`, `model_version`, training data range, number of races in training, and the walk-forward metrics for that model version.
3. **Show model vs baseline.** Add a "vs grid order" column so users can see where the model disagrees with qualifying.
4. **Label simulations.** Name which scenario and parameters were used and don't present a derived result as an independent forecast.
5. **Speed.** Predicting retrains three models every time (`run_pipeline` → `_train_models`). Separate `train` from `predict`, cache the feature table by data hash, and only re-collect sessions that are missing.
6. **Collection robustness.** `collect_historical_data` swallows exceptions as warnings. Count failures and fail the run (or flag it in the output) if a needed session is missing, so a silent gap does not become a silently worse prediction.
7. **Config.** Add Audi, Cadillac and Racing Bulls to `CONSTRUCTOR_COLORS`.
8. **Remove unused dependencies** (`mlflow`, `shap`, `polars`) or start using them.

---

## 6. Tests to add

| Test | What it checks |
|---|---|
| `test_probabilities_consistent` | Win sums to 1, podium sums to 3, `win ≤ podium ≤ top10` for each driver, no NaN |
| `test_point_in_time` | Features for race R are identical when built from the full dataset vs data truncated at R's session start |
| `test_no_zero_position_features` | No `*_avg_position` / `form_*` value of 0 reaches the model |
| `test_rookie_and_new_team` | A driver or team with no history gets neutral values plus missing flags, not elite ones |
| `test_driver_names_resolved` | No `DriverName == Abbreviation` in the latest race |
| `test_team_names_normalised` | No unmapped team names (`Haas F1 Team`, `RB` vs `Racing Bulls`) and lineage keys resolve |
| `test_backtest_does_not_touch_models_dir` | `models/` mtimes unchanged after `--measure-variation` |
| `test_backtest_training_cutoff` | All training rows have `SessionStartUtc` earlier than the held-out race |
| `test_simulator_matches_inputs` | With zero DNF and a very high `pl_temperature`, simulated win rates approach 1/n; with a very low one they approach a deterministic order |
| `test_calibration_oof` | The stored temperature was fitted on out-of-fold scores only |

---

## 7. Suggested order of work

**Step 1: Data and correctness (1-2 days)**
- [ ] P0-1: confirm and fix whole-season collection
- [ ] P0-2: remove `fillna(0)`, add neutral fill and missing flags
- [ ] P0-7: stop backtests overwriting `models/`
- [ ] P1-1, P1-2: driver names from data, team normalisation and lineage
- [ ] P1-4: training cutoff in backtests
- [ ] P0-8: remove or replace the hard-coded importances

**Step 2: Evaluation harness (1-2 days)**
- [ ] Walk-forward backtest by race (section 4.1)
- [ ] Baseline table and reliability tables (4.3, 4.4)
- [ ] Per-run metrics saved to `reports/` and shown in the app

**Step 3: Features (2-3 days)**
- [ ] P0-3: pass laps/weather/messages into `build_features`, add grid and qualifying features, add stages
- [ ] P1-3: era and recency weighting for 2026
- [ ] Speed up `build_features` (avoid O(n²) loop)

**Step 4: Single consistent race model (3-4 days)**
- [ ] P0-5: ranker for strength, DNF model, Plackett-Luce simulation
- [ ] P0-4: out-of-fold temperature and PL temperature
- [ ] P0-6: replace the old simulator and map scenarios onto the new one
- [ ] Remove the separate podium and position models (keep them as baselines)

**Step 5: Polish (ongoing)**
- [ ] UI: uncertainty, provenance, vs-baseline views
- [ ] Circuit features and team-mate decomposition (section 3)
- [ ] MLflow tracking (or remove it), versioned model artifacts
- [ ] Add the tests from section 6 and run them in CI

---

## 8. How to tell it worked

After steps 1-4, re-run the São Paulo 2026 backtest and a full walk-forward run, and check:

1. Win probabilities sum to 1 and podium to 3. No driver has P(win) > P(podium).
2. The top-bin reliability for win and podium probabilities is within about ±0.1 of the observed hit rate.
3. At the `post_quali` stage the model beats the grid-order baseline on win log loss and podium Brier score over the whole walk-forward period (not one race).
4. Rookies and new entries (LIN, CAM, Audi, Cadillac) have sensible, wide-ranging predictions and readable names.
5. Backtests leave `models/` untouched.
