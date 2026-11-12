"""Score persisted pre-race forecasts against completed official classifications.

Usage: python -m scripts.live_scoreboard --forecasts FILE... --results FILE...
Recompute from the forecast archive after each result update. Output is JSON on
stdout, or CSV with --output. Backtests, late forecasts, partial classifications
and unmatched fields are rejected, not presented as live evidence.
"""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd


def score_forecasts(forecasts, results):
    keys = ["Year", "RoundNumber", "SessionType"]
    required = keys + ["Abbreviation", "stage", "model_version", "prediction_generated_at_utc",
                       "training_end_utc", "win_probability", "podium_probability",
                       "top10_probability", "expected_position"]
    missing = set(required) - set(forecasts)
    if missing:
        raise ValueError(f"Forecast provenance is missing: {sorted(missing)}")
    if forecasts.get("variation_measured", pd.Series(False, index=forecasts.index)).fillna(True).any():
        raise ValueError("Backtests are not live forecasting evidence")
    actual = results.copy()
    if "Position" not in actual and "target_position" in actual:
        actual["Position"] = actual.target_position
    for column in keys + ["Abbreviation", "Position", "SessionStartUtc"]:
        if column not in actual:
            raise ValueError(f"Actual classification is missing {column}")
    if actual.duplicated(keys + ["Abbreviation"]).any():
        raise ValueError("Duplicate actual classification rows")
    frame = forecasts.copy()
    for column, default in (("forecast_phase", "post_quali"), ("strength_model", "unknown"),
                            ("scenario", "standard_dry")):
        if column not in frame:
            frame[column] = default
    identity = keys + ["stage", "forecast_phase", "strength_model", "scenario"]
    frame["_generated"] = pd.to_datetime(frame.prediction_generated_at_utc, utc=True, errors="raise")
    # One latest forecast per event/stage/phase, not one row per driver.
    latest = frame.groupby(identity, dropna=False)._generated.transform("max")
    frame = frame.loc[frame._generated.eq(latest)]
    scored = []
    for group, forecast in frame.groupby(identity, dropna=False, sort=True):
        match = actual
        for key, value in zip(keys, group[:3]):
            match = match.loc[match[key].eq(value)]
        if match.empty:
            continue  # Upcoming races have no result and do not enter the denominator.
        n = len(match)
        positions = pd.to_numeric(match.Position, errors="coerce")
        if not np.array_equal(np.sort(positions), np.arange(1, n + 1)):
            raise ValueError("Live scoring requires a complete unique classification 1..N")
        if forecast.Abbreviation.duplicated().any() or set(forecast.Abbreviation) != set(match.Abbreviation):
            raise ValueError("Forecast and result fields must match exactly")
        if forecast.model_version.nunique() != 1:
            raise ValueError("A forecast run cannot mix model versions")
        starts = pd.to_datetime(match.SessionStartUtc, utc=True, errors="raise")
        trained = pd.to_datetime(forecast.training_end_utc, utc=True, errors="raise")
        if starts.isna().any() or starts.nunique() != 1 or trained.isna().any() or forecast._generated.isna().any():
            raise ValueError("Live scoring requires valid timestamps")
        if not (forecast._generated < starts.min()).all() or not (trained < forecast._generated).all():
            raise ValueError("Late forecasts or future-trained models are not live evidence")
        ordered = match.set_index("Abbreviation").reindex(forecast.Abbreviation)
        position = pd.to_numeric(ordered.Position).to_numpy()
        probabilities = forecast[["win_probability", "podium_probability", "top10_probability"]].to_numpy(float)
        if not np.isfinite(probabilities).all() or (probabilities < 0).any() or (probabilities > 1).any():
            raise ValueError("Invalid forecast probabilities")
        if not np.allclose(probabilities.sum(axis=0), [1, min(3, n), min(10, n)], atol=1e-5):
            raise ValueError("Race forecast probability mass is incoherent")
        if not np.isfinite(forecast.expected_position.to_numpy(float)).all():
            raise ValueError("Invalid expected finishing positions")
        if (probabilities[:, 0] > probabilities[:, 1] + 1e-8).any() or (probabilities[:, 1] > probabilities[:, 2] + 1e-8).any():
            raise ValueError("Incoherent forecast probabilities")
        scored.append({**dict(zip(identity, group)), "model_version": forecast.model_version.iloc[0],
                       "forecast_generated_at_utc": forecast._generated.iloc[0].isoformat(),
                       "n_drivers": n, "win_log_loss": float(-np.log(np.clip(probabilities[position == 1, 0][0], 1e-12, 1))),
                       "win_brier": float(np.mean((probabilities[:, 0] - (position == 1)) ** 2)),
                       "podium_brier": float(np.mean((probabilities[:, 1] - (position <= min(3, n))) ** 2)),
                       "top10_brier": float(np.mean((probabilities[:, 2] - (position <= min(10, n))) ** 2)),
                       "position_mae": float(np.abs(forecast.expected_position.to_numpy(float) - position).mean())})
    return pd.DataFrame(scored)


def running_table(scores):
    if scores.empty:
        return pd.DataFrame()
    groups = ["stage", "forecast_phase", "strength_model", "scenario", "model_version"]
    return scores.groupby(groups, dropna=False).agg(
        n_races=("win_log_loss", "size"), win_log_loss=("win_log_loss", "mean"),
        win_brier=("win_brier", "mean"), podium_brier=("podium_brier", "mean"),
        top10_brier=("top10_brier", "mean"), position_mae=("position_mae", "mean")).reset_index()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--forecasts", nargs="+", type=Path, required=True)
    parser.add_argument("--results", nargs="+", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    scores = score_forecasts(pd.concat([pd.read_parquet(path) for path in args.forecasts], ignore_index=True),
                             pd.concat([pd.read_parquet(path) for path in args.results], ignore_index=True))
    if args.output:
        scores.to_csv(args.output, index=False)
    print(json.dumps({"scope": "stored_pre_race_forecasts_only_not_a_skill_claim",
                      "scores": scores.to_dict("records"), "running": running_table(scores).to_dict("records")},
                     default=str, allow_nan=False))


if __name__ == "__main__":
    main()
