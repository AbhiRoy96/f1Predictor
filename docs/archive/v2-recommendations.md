---
title: F1 Predictor v2 Recommendations Archive
description: Historical fixes, data strategy, ensemble and evaluation recommendations
---

> [!NOTE]
> Historical research retained for reference, not a statement of current behavior.
> See the [current guide](../guide.md) for supported workflows.

## F1 Predictor v2: Fixes, Data Strategy, Ensemble and Evaluation Protocol

Applies to the `f1Predictor-master` build, after your `evaluate race 2026 "Brazilian Grand Prix" --stage post_quali` and `--measure-variation` runs on 2026-11-11.

**Evidence labels used below**
- **[Verified]** I read it in the code.
- **[Output]** It follows from the numbers you pasted.
- **[Hypothesis]** Plausible, but needs the one-line check that follows it.

---

## 0. Where you stand

Walk-forward result, 39 held-out races, stage `post_quali`:

| Model | Win log loss | Podium Brier | Top-3 overlap | Spearman | Position MAE |
|---|---|---|---|---|---|
| **race_model** | **1.77** | **0.079** | **1.79** | **0.63** | **3.42** |
| championship baseline | 2.20 | 0.096 | 1.41 | 0.51 | 4.12 |
| team baseline | 2.35 | 0.101 | 1.38 | 0.52 | 4.15 |
| grid baseline | 2.97 | 0.124 | 1.18 | 0.28 | 4.97 (**broken, see F1/F2**) |

Single-race check (São Paulo 2026, held out): Norris win probability 0.385 (he won), position MAE 2.63 (was 4.08 in your first run). Win, podium and top-10 totals are consistent.

The system is in good shape. The remaining work is in three areas:
1. A few real defects that make some numbers misleading (section 2).
2. The shape of your data: it is the wrong shape for a field where drivers and cars keep changing (section 3).
3. A cleaner model architecture, ensemble and train/validation/test protocol so improvements are provable instead of anecdotal (sections 4 and 5).

Keep these numbers as your **before** scoreboard.

---

## 1. Top priorities, in order

| # | Action | Why | Effort |
|---|---|---|---|
| 1 | Fix `grid_position` (all 10.5) and the degenerate grid baseline (F1, F2) | Model lacks its strongest feature; the "beats grid" claim is inflated | 1-2 h |
| 2 | Collect **all rounds of the current season and the previous one**, results-only (section 3) | Your 2026 data is 6 of about 19 races; 2021 has equal weight | 0.5-1 day |
| 3 | Re-split by **time, not by count**: time-decay weights and a rolling-window ablation (3.4, 3.6) | Tests your instinct that less history can be better, with evidence | 0.5 day |
| 4 | Split into an entity-agnostic structure layer and a short-memory entity layer (section 4) | Matches how the field actually changes | 1-2 days |
| 5 | Three-member ensemble fitted on out-of-fold scores, plus a locked final holdout (sections 4.4 and 5) | Prevents overfitting to 39 races | 1 day |
| 6 | Audit DNF labels (F4) | Retirement rates of 12-32% look too high | 2 h |

---

## 2. Fix list

### F1. `grid_position` is 10.5 for every driver **[Output + Verified]**

**Symptom.** In your `--measure-variation` output, `vs_grid_order + expected_position = 10.5` for all 22 drivers (for example NOR: 4.132 + 6.368; ANT: 4.819 + 5.681; STR: 17.352 + (−6.852)). That means the grid value used was identical for everyone.

**Cause.** In `engineer_features.py` `_stage_features`, `grid_position` is taken from the qualifying session's `GridPosition` column (and from `GridPublishedAt` for the target row). Qualifying results usually have no official grid column filled, so the value stays NaN. `_neutral_positions` then replaces NaN with `NEUTRAL_POSITION = 10.5`. Consequences:
- The model never sees a real grid position (it still sees `quali_position` and the qualifying time gaps, so the damage is partial).
- `grid_penalty_places` and `pit_lane_start` can never populate.
- `vs_grid_order` in the output is meaningless.

**Confirm (30 seconds):**

```python
import pandas as pd
f = pd.read_parquet("data/processed/race_post_quali_features.parquet")
print(f.grid_position.value_counts().head())     # expect a single value 10.5 if the bug is real
print(f.quali_position.nunique(), f.quali_gap_to_pole_pct.notna().mean())
```

**Fix, minimal (quali order as a proxy for grid):** in `_stage_features`, initialise a flag with the other feature defaults and set the proxy after the penalty calculation.

```python
# with the other defaults at the top of _stage_features
features["grid_is_quali_proxy"] = 0

# after the grid_penalty_places block
if pd.isna(features["grid_position"]) and pd.notna(features["quali_position"]):
    features["grid_position"] = features["quali_position"]
    features["grid_is_quali_proxy"] = 1
```

**Fix, better:** for **completed** historical races, the race result row's `GridPosition` is the true start order (including penalties and pit-lane starts) and is fixed before lights out. Use it for historical rows with `GridPublishedAt` set to the qualifying end time, so the existing cutoff guard still applies. For the live forecast, use the proxy until the official grid is published (penalties usually appear a few hours after qualifying).

**Add a test:** `grid_position` must have more than 5 distinct values in every `post_quali` race with 18 or more drivers, and `grid_is_quali_proxy` must equal 1 only for rows where the official grid is missing.

### F2. The grid baseline is degenerate **[Output + Verified]**

`_baseline_strength("grid")` picks the first existing candidate column, `grid_position`. Because of F1 it is constant, so every driver gets the same strength and probabilities are uniform. That matches your output: grid win log loss 2.97 (about ln 20 = 3.0), a single reliability bin with mean_p = hit_rate = 0.049, and a Spearman of 0.28. A real grid baseline should score much better.

```python
# evaluate._baseline_strength
column = next((c for c in candidates
               if c in df and pd.to_numeric(df[c], errors="coerce").nunique() > 1), None)
```

Also add a **second grid baseline that uses `quali_position` explicitly**, so you always have one non-degenerate "starting order" reference even if the grid column breaks again.

**Expect:** the grid baseline improves a lot and the margin over it shrinks. The honest benchmark is then `max(grid, championship)`.

### F3. Compare against the strongest baseline, not only against grid **[Verified]**

`bootstrap_race_metrics` reports paired differences only against `grid`. Make the reference configurable and report paired differences against the **best baseline per metric** (after F2 this will often be the quali-order baseline):

```python
reference_name = metrics.groupby("model").win_log_loss.mean().drop("race_model").idxmin()
```

Report: estimate, 95% block-bootstrap CI, and whether the CI excludes 0. Currently the absolute CIs for race_model vs championship overlap on win log loss ([1.45, 2.13] vs [1.74, 2.56]), so that particular win is **not yet established**. The podium Brier and Spearman gaps look clearer.

### F4. DNF labels and the DNF model **[Output + Hypothesis]**

Observations from your reliability tables:
- DNF Brier 0.1396 is about equal to a constant-rate guess. With an observed rate near 0.167 across the 640 labelled rows, `p(1−p) ≈ 0.139`. So the model shows **no measurable skill beyond the base rate**.
- Predicted rates (12-32%) are high compared with what you would normally expect (roughly 8-10% per driver). Observed hit rates sit above predicted in every bin (about 0.16 vs 0.14), so the model is also slightly **under**-predicting the rate in your own data. That means the labelled data itself looks high.
- Only 640 of 792 rows have a DNF label.

Two possible causes:
1. **Sampled circuits are chaotic.** Your six events per season (Brazil, Mexico, Singapore, Austria, Britain, USA and similar) over-represent high-incident tracks.
2. **Unlabelled rows are mostly finishers.** `dnf_labels` treats only `finished` or `+N lap(s)` as finished. If FastF1 returns statuses like `Lapped` for lapped finishers, they are neither "finished" nor "retired", so they drop out of the denominator and inflate the DNF rate.

**Check:**

```python
from src.features.engineer_features import load_results
r = load_results()
print(r.loc[r.SessionType == "R", "Status"].value_counts(dropna=False).head(25))
```

**Fix (in `src/data/outcomes.py`):**

```python
finished = status.str.fullmatch(r"finished|lapped|\+\d+ laps?", na=False)
not_a_retirement = status.str.contains(r"disqualif|excluded|did not start|did not qualify|withdrew", na=False)
# these are neither finishers nor retirements: leave the label unknown (NaN), do not count as DNF
```

**Then add a skill score** to evaluation:

```python
base_rate = train_dnf_rate                      # from the training window only
brier_const = base_rate * (1 - base_rate)
dnf_skill = 1 - dnf_brier / brier_const         # > 0 means better than constant
```

**Improve the DNF model (see section 4.5).** Use results from all circuits and all rounds (cheap, see F7) so the base rates are unbiased.

### F5. Plackett-Luce temperature is tuned on the wrong target **[Output + Verified]**

`finisher_pl_nll_diagnostic` for race_model is **worse** than every baseline (26.8 vs 22.7 for championship; its CI against grid is [−1.8, +6.2]). The model is good at win/podium but is not better at ordering the full field of finishers. The temperature objective in `calibrate.fit_simulation_temperature` is "final win plus podium log loss", which is top-heavy and noisy because it uses 1,024 simulations.

**Change the objective to the analytic top-10 Plackett-Luce log-likelihood.** It needs no simulation, so it has no Monte Carlo noise, and it weights the whole top of the order:

```python
import numpy as np
from scipy.optimize import minimize_scalar

def pl_topk_nll(scores, finish_order, T=1.0, k=10):
    """scores: strengths per driver (higher = faster). finish_order: driver indices in observed
    finishing order, classified finishers first. Returns the negative log-likelihood of the first k places."""
    z = scores[finish_order] / T
    rev_lse = np.logaddexp.accumulate(z[::-1])[::-1]     # log-sum-exp of z[j:], for each j
    return -(z[:k] - rev_lse[:k]).sum()

def fit_pl_temperature(oof_races, k=10):
    """oof_races: list of (scores, finish_order) from out-of-fold predictions only."""
    f = lambda logT: sum(pl_topk_nll(s, o, np.exp(logT), k) for s, o in oof_races)
    return float(np.exp(minimize_scalar(f, bounds=(-2, 2), method="bounded").x))
```

Keep the simulation-based check afterwards: simulated win/podium/top-10 marginals should have reliable calibration on out-of-fold races. If the top-10 objective improves the full-order diagnostic but worsens win calibration, blend the two objectives (for example 0.5 × top-10 NLL + 0.5 × win/podium log loss) and choose by the dev walk-forward result, not by looking at the holdout.

### F6. SettingWithCopyWarning **[Output + Verified]**

`event_selection.py:61` (`events["_circuit"] = ...`) writes to a slice in the target-season branch. Harmless today, but it can silently misbehave in newer pandas.

```python
if collection_year == target_year:
    events = events.loc[events.RoundNumber.le(int(target.RoundNumber))].copy()
```

### F7. Results-only collection for most of the history **[Verified, needs a small code change]**

`collect_event` always calls `session.load(laps=True, telemetry=False, weather=True, messages=True)`. That is the slow part of collection. Most of your signal (finishing order, qualifying order and times, status, grid) lives in results. Add a `lightweight` flag:

```python
# collect_season.collect_event(..., lightweight=False)
needs_timing = (not lightweight) or session_type in {"FP1", "FP2", "FP3"}
session.load(laps=needs_timing, telemetry=False, weather=needs_timing, messages=False)
```

Check the surrounding lines for any code that assumes `session.laps` exists before using this (it is a sketch, not tested).

In `collect_historical_data`, split the plan into two tiers:

| Tier | Which events | Which sessions | Why |
|---|---|---|---|
| Rich | Last 6-8 rounds before the target, plus the target weekend | FP1-3, Q, SQ, S, R with laps and weather | Practice pace and weekend features |
| Light | **Every other round of the current season and the whole previous season** (and optionally older seasons at low weight) | Q, SQ, S, R, results only | Entity strength, DNF rates, structure |

`collect_event(..., session_types={"R","Q","S","SQ"})` already supports session filtering.

### F8. Output readability **[Output]**

- Print probabilities as decimals, not `3.85e-01`: `predictions.to_string(index=False, float_format=lambda x: f"{x:.3f}")`.
- `p10_position` and `p90_position` include retirements, so p90 is 18-21 for almost everyone. Also report quantiles **conditional on finishing**, so the interval says something about pace:

```python
# in summarize_positions, from the simulated positions and retired mask
finish_pos = np.where(retired, np.nan, positions)
p10_finish = np.nanpercentile(finish_pos, 10, axis=0)
p90_finish = np.nanpercentile(finish_pos, 90, axis=0)
```

### F9. Sanity-check one suspicious output **[Output]**

Piastri has win 0.002 and expected position 11.0, next to his teammate Norris at 0.385. That can be right (a bad qualifying, a penalty), but check it in the feature table:

```python
cols = ["Abbreviation","quali_position","quali_gap_to_pole_pct","quali_gap_to_teammate_pct",
        "driver_race_form_5","driver_race_ewma_position"]
print(f[(f.Year==2026)&(f.RoundNumber==f.RoundNumber.max())][cols])
```

A 200× gap between teammates should always be explainable by a specific feature value.

Add a general **audit script** (`scripts/audit_features.py`) that you run after every data refresh:

```python
import pandas as pd
f = pd.read_parquet("data/processed/race_post_quali_features.parquet")
latest = f[(f.Year == f.Year.max()) & (f.RoundNumber == f[f.Year == f.Year.max()].RoundNumber.max())]
report = {}
for c in ["grid_position","quali_position","quali_gap_to_pole_pct","quali_gap_to_teammate_pct",
          "fp2_long_run_pace_rank","fp_best_lap_gap_pct"]:
    if c not in f:
        report[c] = "MISSING COLUMN"
        continue
    report[c] = f"distinct={f[c].nunique()}  nan={f[c].isna().mean():.0%}  latest_race_distinct={latest[c].nunique()}"
print(pd.Series(report).to_string())
print("Races per year:\n", f[f.SessionType == "R"].groupby("Year").RoundNumber.nunique())
print("Drivers with < 10 career starts in latest race:",
      int((latest.get("driver_race_career_sessions", pd.Series(dtype=float)) < 10).sum()))
```

### F10. Housekeeping **[Verified, check with grep before deleting]**

- `src/models/train_winner.py`, `train_podium.py`, `train_position.py` and the committed `models/*.joblib` files are not used by `main.py`. Delete them (or move them to `legacy/`) to avoid confusion. `models/` should be in `.gitignore`.
- `_data_hash` re-reads every raw parquet on each run. Hash `(path, size, mtime_ns)` instead; keep the content hash only for the code files.
- If `src/models/baselines.py` is no longer imported (evaluate defines its own baselines), remove it or reuse it, so there is one definition of each baseline.

---

## 3. Data strategy: less history, but the right history

Your instinct is right. The question is not "how much history" but "history of what". Two findings from your own logs:

**Finding 1. The current season is badly under-represented [Output].**
Your collection plan for 2026 is: Austrian, British, Singapore, US, Mexico City, São Paulo. That is 6 of the roughly 19 races run before São Paulo. So the data about *today's* cars, drivers and regulations is about a third complete, while 2021 (different regulations, different cars, a different grid) gets the same budget, 6 races.

**Finding 2. All "last N" windows are in units of sampled races, not time [Verified].**
- `form_5` and `form_3` are rolling windows over rows, `ewma` uses `halflife=5` rows.
- `recency_weights` uses a half-life of 30 *races in the training frame*.

With about 6 races per season in the frame, "last 5" can span most of a year and "half-life 30" is about five calendar years. That is far too long for a field where drivers and teams change, and the weighting means something different every time you change `--events-per-season`.

### 3.1 Principle: two layers with different memory

| Layer | Question it answers | Needs | Memory |
|---|---|---|---|
| **Structure** | "How does qualifying gap, grid, pace rank, circuit and reliability translate into a finishing order?" | Many races, any era, **no driver or team identity** | Long (all eras, light weights) |
| **Entity strength** | "How fast is this driver-in-this-car **right now**?" | Recent races of the current cars | Short (months), updated sequentially |

Mixing both in one flat feature table (career wins, circuit history per driver, era averages) forces the model to learn identity-based rules from very few rows per entity. With a changing pool, those rows are mostly stale. The two-layer split handles new drivers and teams naturally (section 3.5).

### 3.2 Recommended data window

| Data | Window | Sessions | Weight |
|---|---|---|---|
| Current season | **All rounds up to the target** | R, Q, S, SQ (results); FP/laps/weather for the last 6-8 rounds | Full |
| Previous season | **All rounds** | R, Q, S, SQ (results only) | Time-decayed (half-life around 9 months) |
| Two to five seasons back | Optional | R, Q results only | Low and entity-agnostic features only |

That is roughly 20 + 24 = 44 core races instead of 36 mostly-old sampled ones, with the most relevant ones dominating. Expect a few minutes of first-time collection (FastF1 caches afterwards).

### 3.3 Handling the regulation reset (2026)

- Treat 2026 as a new era for **constructor strength**: constructor effects should restart from a wide prior (your Bayesian model already does this at 2022 and 2026 with retention 0.25; keep it).
- Keep **driver skill** across eras with a narrower prior: drivers carry over, cars do not.
- Reliability is also era-dependent: early-season mechanical failure rates are higher under new regulations. Use `rounds_into_era` (you already compute it) as a feature of the **mechanical** DNF component.
- Structure-layer features (see 4.2) are relative within a race, so they transfer across eras without special handling.

### 3.4 Use calendar time, not race counts, for recency

```python
import numpy as np
import pandas as pd

def time_decay_weights(start_utc, reference_utc, half_life_days=270, floor=0.02):
    age_days = (pd.to_datetime(reference_utc, utc=True) - pd.to_datetime(start_utc, utc=True)
                ).dt.total_seconds().to_numpy() / 86400
    return np.maximum(0.5 ** (np.clip(age_days, 0, None) / half_life_days), floor)
```

- Compute weights relative to the **training cut-off** (the forecast race start), never to the latest date in the file.
- Do the same for form features: replace `rolling(5)` and `ewm(halflife=5)` with time-based versions (`ewm(halflife="120D", times=SessionStartUtc)`), so their meaning does not depend on how many events you collected.
- Treat the half-life (try 180, 270, 400, 730 days) as a **tuned hyperparameter** (section 3.6).

### 3.5 New drivers and teams (Lindblad, Câmara, Cadillac, Audi and every future rookie)

1. **Teammate-relative information is the best early signal.** `quali_gap_to_teammate_pct` and `fp_teammate_gap_pct` identify driver skill inside a shared car. Keep them in the structure layer.
2. **Rookie priors in the entity layer.** Start a new driver at the field-average driver effect with a **wider** variance, and let the process variance decay with experience:

```python
def driver_process_sd(starts, base=0.035, boost=0.12, tau=8):
    return base + boost * np.exp(-starts / tau)      # larger drift in the first few starts
```

3. **New constructors** start from a wide prior centred on a typical midfield team, then update quickly (use the same experience-scaled variance, counted in team-races).
4. **Driver moves teams:** keep the driver effect, reset only the constructor effect (you already identify driver and constructor effects separately; verify with a test that Hamilton-style moves do not reset the driver component).
5. **Stop using identity-sparse features for newcomers.** `career_*` and per-driver circuit history are NaN or tiny for newcomers. Rely on relative features and the entity layer for them, and keep the missing-value flags.
6. **Evaluate newcomers separately** (section 6): slice metrics by `career_starts < 10`. If they are much worse, raise the rookie prior variance.

### 3.6 Window ablation: let the data decide

Implement a rolling **training window** in the walk-forward loop. It must apply per held-out race (relative to that race's start). A global "years ≥ X" filter would leak.

```python
def window_mask(df, race_start, window_days=None):
    start = pd.to_datetime(df.SessionStartUtc, utc=True)
    m = start < race_start
    if window_days is not None:
        m &= start >= race_start - pd.Timedelta(days=window_days)
    return m

WINDOWS = {"12m": 365, "18m": 548, "24m": 730, "36m": 1095, "all": None}
HALF_LIVES = [180, 270, 400, 730]
```

Run the same walk-forward for each (window, half-life) pair using **only the dev period** (section 5), then fill in:

| Window | Half-life | Win log loss | Podium Brier | Top-10 PL NLL | Newcomer slice | CI vs best baseline |
|---|---|---|---|---|---|---|
| 12m | 180 d | | | | | |
| 24m | 270 d | | | | | |
| 36m | 400 d | | | | | |
| all | 730 d | | | | | |

**Decision rule:** pick the shortest window whose top-10 PL NLL is within one standard error of the best. Prefer the simpler/shorter option when results are close. Do not tune beyond this grid.

---

## 4. Model architecture

### 4.1 Target design

```
              ┌────────────────────────────── structure layer (long memory) ─────────────────────────────┐
 results,     │  relative features: quali gap %, teammate gap %, pace rank, penalties, circuit effects     │
 quali, FP  ──┤  → conditional-logit (lean) + XGBRanker (non-linear)                                        │
              └───────────────────────────────────────────────────────────────────────────────────────────┘
              ┌────────────────────────────── entity layer (short memory) ───────────────────────────────┐
 results      │  DynamicBayesianStrength: driver + constructor effects, random walk, era resets             │
 (time-       └───────────────────────────────────────────────────────────────────────────────────────────┘
  ordered)    ┌────────────────────────────── retirement layer ──────────────────────────────────────────┐
              │  mechanical (constructor + era), collision (driver + grid + circuit), other                  │
              └───────────────────────────────────────────────────────────────────────────────────────────┘
                              │ strengths (OOF-fitted blend)        │ retirement probabilities
                              └──────────────► Plackett-Luce simulation (mixture, correlated risk) ◄───────┘
                                                      │
                          win / podium / top-10 / position distribution (sums by construction)
```

You already have most blocks: the Bayesian strengths, the XGB ranker, the DNF model, the simulator. The changes are to (a) clean the features each member sees, (b) add one cheap stable member, and (c) fit the blend and temperature on proper out-of-fold scores.

### 4.2 Feature rules for the structure layer

**Keep (relative or within-race):**
- `grid_position` (real, after F1), `quali_position`, `quali_gap_to_pole_pct`, `quali_gap_to_teammate_pct`, `grid_penalty_places`, `pit_lane_start`
- `fp2_long_run_pace_rank`, `fp_best_lap_gap_pct`, `fp_teammate_gap_pct`
- Time-decayed form such as `ewma_finish_vs_grid` (race craft), `ewma_position` (as a rank within the race, see below)
- `circuit_overtaking_position_change`, `circuit_dnf_rate_shrunk`
- `rounds_into_era`, `*_is_missing` flags

**Transform:** any absolute average position or points feature should also be expressed **relative to the race field**: z-score or rank within the race, and gap to the field's best/median. A P3 average means different things in different eras.

**Drop from the ranker and the logit (try an ablation first):** `career_*` counts and averages, per-driver circuit history, `era_strength_*` raw values. They describe identity, not current ability. The entity layer already covers that information in a better-regularised way.

### 4.3 Ensemble members

| Member | Layer | What it captures | Data window | Include? |
|---|---|---|---|---|
| **DynamicBayesianStrength** (exists) | Entity | Latent driver and team pace with drift and era resets | Long, self-decaying | **Yes** |
| **Conditional logit on a lean feature set** (exists in legacy, restore) | Structure | Linear effect of quali, grid, pace; very stable on small data | 18-36 months, time-decayed | **Yes** |
| **XGBRanker** (exists) | Structure | Non-linear interactions (grid × circuit overtaking, pace × tyre) | 18-36 months, time-decayed | **Yes** (depth 3, strong regularisation, 3 seeds averaged) |
| Quali-order PL baseline | Reference | Starting order | n/a | Keep as a baseline, not a member |
| LightGBM LambdaRank / CatBoost YetiRank | Structure | More of the same as XGB | | **Skip.** Little diversity, more variance |
| Another rating system (Elo, TrueSkill-style) | Entity | Same idea as the Bayesian model | | **Skip.** Your Bayesian model is already the dynamic version of this |
| Deep learning (LSTM, transformer, embeddings) | | | | **Not now** (section 4.6) |

Three members at most. With about 40-70 races, every extra member adds weight-estimation noise that costs more than it brings.

### 4.4 How to fit the ensemble

Rules:
1. **Blend in score space (log-strength), not in probability space.** All members score drivers within a race. Standardise each member's scores per race first (subtract the race mean, divide by a training-set scale you store).
2. **Weights come from out-of-fold scores only.** Each race's scores are produced by a model that never saw that race (section 5).
3. **Constrain the weights:** non-negative, summing to 1 (softmax parameterisation), with a small pull towards equal weights.
4. **Objective:** top-10 Plackett-Luce NLL (analytic, see F5), not simulated win/podium loss. It is smoother and uses far more of each race.
5. **Fit the temperature after the weights**, on the same OOF scores.
6. **Check weight stability:** refit on bootstrap samples of races. If any weight swings from 0 to 1, use equal weights.

```python
import numpy as np
from scipy.optimize import minimize

def fit_blend_weights(oof_scores, oof_orders, k=10, pull=0.05):
    """oof_scores: list over races of arrays (n_drivers, n_members), standardised per race.
       oof_orders: list over races of finishing orders (driver indices, classified finishers only)."""
    m = oof_scores[0].shape[1]

    def loss(theta):
        w = np.exp(theta - theta.max()); w /= w.sum()
        total = 0.0
        for S, order in zip(oof_scores, oof_orders):
            total += pl_topk_nll(S @ w, order, T=1.0, k=k)
        return total / len(oof_scores) + pull * np.sum((w - 1 / m) ** 2)

    res = minimize(loss, np.zeros(m), method="Nelder-Mead", options={"maxiter": 400})
    w = np.exp(res.x - res.x.max())
    return w / w.sum()
```

**Disagreement as uncertainty (optional, test it first).** The default blends scores to one point forecast. Alternatively, draw the "true model" per simulation from the weights, so model disagreement widens the distribution:

```python
# inside the simulator, scores: (n_drivers, n_members), w: member weights
comp = rng.choice(len(w), size=n_sims, p=w)        # one member per simulated race
sampled = scores.T[comp]                           # (n_sims, n_drivers)
util = sampled / pl_temperature + rng.gumbel(size=(n_sims, n_drivers))
```

Re-fit the temperature for the mixture variant and keep whichever (point blend or mixture) has the better OOF top-10 NLL and win/podium reliability.

### 4.5 Retirement (DNF) layer

Use all circuits and all rounds (F7) so base rates are unbiased. Keep the three causes you already model, and give each its own drivers of risk:

| Cause | Main drivers | Notes |
|---|---|---|
| Mechanical | Constructor lineage, `rounds_into_era`, circuit stress | Higher early in a new era; time-decayed; shrink hard (prior of about 20 starts) |
| Collision/accident | Driver incident rate, grid position (midfield start is riskier), circuit, wet flag | Driver-level and heavily shrunk; avoid over-trusting small samples |
| Other | Base rate | Penalties, illness, etc. |

Evaluate with the **skill score** from F4 (versus a constant rate). If the skill stays near zero, keep the model simple (a shrunk constant per cause plus the era effect) rather than adding features.

### 4.6 Deep learning: an honest assessment

You have roughly 1,000-3,000 driver-race rows, about 20 per race, and strong dependence inside a race. That is **small tabular, ranking data**. Deep models (LSTM, transformers, large embeddings) will usually overfit it and rarely beat a regularised linear or Bayesian model there, while adding training time, seed variance and more things to validate. The Bayesian layer is already the "learned embedding with dynamics" version of what a neural net would try to learn.

**Reasonable bar for adding one:** only a small listwise network, using the Plackett-Luce loss so it matches your simulator, trained with heavy dropout and weight decay, as a **fourth member** that must earn its place in the OOF walk-forward. Include it only if it improves top-10 PL NLL with a bootstrap CI that excludes 0 against the 3-member blend. Otherwise drop it.

```python
# optional dependency: pip install torch
import torch, torch.nn as nn

class RaceMLP(nn.Module):
    def __init__(self, d_in, hidden=32, p=0.3):
        super().__init__()
        self.net = nn.Sequential(nn.Linear(d_in, hidden), nn.ReLU(), nn.Dropout(p), nn.Linear(hidden, 1))
    def forward(self, x):                      # x: (n_drivers, d_in) for ONE race
        return self.net(x).squeeze(-1)

def pl_loss(scores, finish_rank):              # finish_rank: 1 = winner; classified finishers only
    s = scores[torch.argsort(finish_rank)]
    rev_lse = torch.logcumsumexp(s.flip(0), 0).flip(0)
    return -(s - rev_lse).sum()
```

Training loop: one race per step, AdamW with `weight_decay=1e-2`, early stopping on a forward-chained validation block, 5 seeds averaged. Expect it to roughly tie the conditional logit. That is the likely outcome, so do not plan around it.

---

## 5. Train / validation / test protocol

A random train/test split is wrong here. Races are ordered in time, drivers and cars repeat, and results within a weekend are correlated. Use **time-ordered splits with a locked final test**.

### 5.1 Layout

```
 time ───────────────────────────────────────────────────────────────────────────►
 │◄──────────────────────── DEV (tune everything here) ──────────────────────►│◄─ FINAL TEST ─►│
 │  walk-forward folds: train on [past window] → predict next race(s) → OOF   │  last ~8 races  │
 │  used for: window, half-life, weights, temperature, hyper-params, features │  touched ONCE   │
```

- **Final test:** the last ~8 completed races (today that is roughly rounds 13-20 of 2026, including São Paulo). Define the cut-off date in config and never tune on it. If you must look at it, log that you did.
- **Dev:** everything before. Inside dev use **expanding or rolling-window walk-forward** with a refit every 2-4 races.
- **Group by weekend.** A sprint and its race are one unit. Never split them across train and test.
- **Embargo:** train only on sessions that started strictly before the forecast race's feature cut-off (your `FeatureCutoffUtc` logic already does this).

### 5.2 Step by step

1. **Freeze the primary metric** before experimenting: top-10 PL NLL (per race, averaged), with win log loss and podium Brier as secondary. Write it into the config.
2. **Window and half-life ablation** on dev only (3.6).
3. **Feature ablation** on dev: drop `career_*`, add relative transforms. At most 4-6 variants.
4. **Fit blend weights and temperature** from dev OOF scores (4.4).
5. **Freeze** the configuration. Record it (config hash, code SHA, data hash).
6. **Evaluate once on the final test.** Report the paired differences vs the best baseline with block-bootstrap CIs.
7. **Deploy:** refit on dev + final test, using the frozen configuration, and forecast the next race.
8. **Forward test (the real out-of-sample):** store every forecast **before** the race, then score it afterwards (5.4).

### 5.3 Walk-forward with an optional window, final-test aware

```python
def walk_forward_dev(df, fit, predict, *, test_start, min_train_races=12, refit_every=3,
                     window_days=None, half_life_days=270):
    """Expanding or rolling-window walk-forward over the dev period only."""
    races = (df[df.SessionStartUtc < test_start]
             .drop_duplicates(["Year", "RoundNumber"])
             .sort_values("SessionStartUtc")[["Year", "RoundNumber", "SessionStartUtc"]])
    oof, model = [], None
    for i, (y, r, start) in enumerate(races.itertuples(index=False)):
        if i < min_train_races:
            continue
        if model is None or (i - min_train_races) % refit_every == 0:
            train = df[window_mask(df, start, window_days)]
            w = time_decay_weights(train.SessionStartUtc, start, half_life_days)
            model = fit(train, sample_weight=w)
        test = df[(df.Year == y) & (df.RoundNumber == r)]
        oof.append(predict(model, test).assign(Year=y, RoundNumber=r))
    return pd.concat(oof, ignore_index=True)
```

Notes:
- Make the OOF predictions from **the same code path as production** (same imputation, same features, same temperature step), so the validation measures the deployed model.
- If a member is refit every 3 races, its OOF scores for the 2 races in between come from the older fit. That is fine and realistic.

### 5.4 Live scoreboard

Add `scripts/score_forecasts.py`: for every saved prediction file whose race has finished, join the actual results and append one row per race to `reports/live_scoreboard.parquet` (win log loss, podium Brier, top-10 PL NLL, MAE, plus the best baseline's numbers). After 15-20 live races this is the most trustworthy evidence you will have.

### 5.5 Guarding against overfitting to a small test

- With about 40-70 races, **limit the experiments**: at most about 8 variants in total, pre-written in a table. Count them.
- Prefer **paired** differences with a block bootstrap (block length of about 4 races) over comparing two overlapping absolute CIs.
- Report the number of variants tried next to any "significant" result.
- Do not choose between close alternatives on the final test. Choose on dev, confirm on test.
- Average 3-5 seeds for any stochastic member.

---

## 6. Evaluation upgrades

| Add | Why |
|---|---|
| Paired CI vs the **best** baseline (F3) | Currently only vs grid |
| Second grid baseline from `quali_position` (F2) | A non-degenerate starting-order reference |
| DNF skill score vs constant rate (F4) | Shows whether the DNF layer helps at all |
| Top-10 PL NLL as primary metric (F5) | Smoother and covers the whole top order |
| Slice: newcomers (`career_starts < 10`) vs veterans | Tests the changing-pool problem directly |
| Slice: 2026 only vs earlier seasons | Tests regulation-reset behaviour |
| Slice: by circuit family (street, high-speed, technical) | Shows where the model struggles |
| Slice: by stage (`pre_weekend`, `post_fp`, `post_quali`) | Value of each information stage |
| Win calibration table with few, wide bins (for example 0-5%, 5-15%, 15-30%, 30%+) | Your top win bins have 2-7 races each, so ten bins are noise |
| Per-driver and per-team signed error (rolling) | Systematic bias is more informative than MAE |

**Reading your current calibration tables [Output]:**
- Win: roughly fine at low probabilities. The 0.2-0.3 bin hit rate is 0.39 against a mean prediction of 0.25 (n = 28, mildly under-confident). The bins above 0.5 hold 10 samples in total, so no conclusion yet.
- Podium: close to the diagonal up to 0.8; the 0.8-0.9 bin shows 0.73 vs 0.84 (n = 15).
- Top-10: the lowest bin shows 0.13 observed vs 0.054 predicted (n = 97), so long shots enter the points more often than predicted. This is a tail-heaviness sign. It would improve if the temperature objective includes top-10 (F5) and DNF labels are fixed (F4).

---

## 7. Roadmap

### Phase 1: correctness (1 day)
- [ ] F1 grid fix and test; F2 baseline fix; re-run `evaluate`
- [ ] F4 DNF audit (`Status.value_counts()`), fix regex, add skill score
- [ ] F6 `.copy()`; F8 output formatting and conditional quantiles
- [ ] F9 audit script; check Piastri's features
- [ ] F10 housekeeping

**Done when:** `grid_position` has many distinct values, the grid baseline is no longer uniform, and the new scoreboard is recorded.

### Phase 2: data shape (1 day)
- [ ] F7 lightweight collection; collect all rounds for the current and previous season (results-only)
- [ ] Time-based form and decay (3.4)
- [ ] Window mask and `walk_forward_dev` (5.3)

**Done when:** `groupby(Year).RoundNumber.nunique()` shows the full current season up to the target and the previous season.

### Phase 3: ablation on dev (1 day)
- [ ] Window × half-life grid (3.6)
- [ ] Feature variants: drop `career_*`, add relative transforms (4.2)
- [ ] Choose by the primary metric, recorded in a table

**Done when:** a written table with the chosen window, half-life and feature set.

### Phase 4: ensemble and calibration (1-2 days)
- [ ] Restore the conditional logit member; average 3 seeds for XGB
- [ ] OOF weights (4.4) and top-10 PL temperature (F5)
- [ ] Compare the point blend and the mixture simulation
- [ ] Rookie priors in the Bayesian model (3.5)

**Done when:** OOF reliability tables are acceptable and the blend weights are stable under bootstrap.

### Phase 5: lock and measure (0.5 day)
- [ ] Define the final-test cut-off; run once; write the report
- [ ] Refit on everything and deploy
- [ ] Live scoreboard script (5.4)

### Phase 6: optional
- [ ] Listwise MLP member (4.6), only if time allows and only if it earns its place
- [ ] Learned circuit features (overtaking difficulty and safety-car rate by circuit, from all rounds)
- [ ] Feature attribution per driver (components of strength: qualifying, team, form) in the app

---

## 8. Tests to add

| Test | Assertion |
|---|---|
| `test_grid_position_populated` | `post_quali` features have more than 5 distinct `grid_position` values per race; proxy flag only where the official grid is missing |
| `test_grid_baseline_not_degenerate` | Baseline strengths are non-constant; baseline win log loss is below ln(n) |
| `test_dnf_lapped_is_finished` | `Lapped` and `+1 Lap` are not DNF; `Disqualified` yields an unknown label |
| `test_time_decay_reference` | Weights depend only on time before the forecast race, not on later data |
| `test_window_no_leak` | Every training row precedes the held-out race start; none lies outside the window |
| `test_final_holdout_untouched` | No dev routine reads rows after `test_start` |
| `test_blend_weights_valid` | Weights are non-negative, sum to 1 and are derived from OOF scores only |
| `test_pl_topk_nll_matches_bruteforce` | Analytic top-k NLL equals brute force for small fields |
| `test_rookie_prior` | A driver with 0 starts gets a wider posterior than one with 40 |
| `test_probabilities_sum` | Win sums to 1, podium to 3, top-10 to 10, and win ≤ podium ≤ top-10 (keep the existing check) |

---

## 9. What I would not do

- **Random K-fold or shuffled train/test splits.** They leak across time and entities.
- **More features before the data shape is fixed.** Most gains come from relevance of data and from calibration, not from feature count.
- **A deep network as the main model.** See 4.6.
- **Choosing between close variants on the final test.**
- **Calibrating on in-sample scores.** Your current pipeline already avoids this; keep it that way for every new member.
- **Collecting more old seasons "just in case".** More old rows mostly add identity noise. Add them only as low-weight structure data if the ablation shows a benefit.

---

## 10. Expected outcome, honestly

- F1/F2 will probably **lower** your apparent margin over the grid baseline while making the model somewhat better in absolute terms.
- Moving from 6 sampled races per season to all current-season races should help most on newcomers and on rapidly changing teams, but the size of the effect is unknown until the window ablation runs.
- The ensemble should give a modest, stable gain and, more importantly, more trustworthy probabilities. It will not turn win prediction into a solved problem: even a perfect model faces safety cars, strategy and reliability, so a favourite at 40-50% is realistic.
- Judge progress by the **live scoreboard** over the next 15-20 races, not by any single backtest.
