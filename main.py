"""Point-in-time F1 collection, training, prediction and evaluation commands."""

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import fastf1
import joblib
import numpy as np
import pandas as pd

from src.data.collect_driver_constructor_stats import collect_driver_constructor_stats
from src.data.collect_season import _resolve_event, _session_specs, collect_event
from src.data.event_selection import (
    DEFAULT_EVENTS_PER_SEASON,
    select_related_events,
    similar_circuit_keys,
)
from src.data.normalise_entities import normalise_driver_name
from src.data.validate_data import validate_features_df
from src.features.engineer_features import build_features, load_results
from src.utils.config import PREDICTIONS_DATA_DIR, PROCESSED_DATA_DIR, RAW_DATA_DIR
from src.utils.logging import setup_logger

logger = setup_logger("f1_orchestrator")
HISTORICAL_START_YEAR = 2021
MODELS_DIR = Path("models")
REPORTS_DIR = Path("reports")
STAGES = ("pre_weekend", "post_fp", "post_quali")
SESSION_TYPES = {"race": ("R", "race"), "sprint": ("S", "sprint"),
                 "qualifying": ("Q", "qualifying")}


def _missing_sessions(year, event, now=None):
    """Return elapsed sessions without persisted results (or practice laps)."""
    now = pd.Timestamp.now(tz="UTC") if now is None else pd.to_datetime(now, utc=True)
    missing = set()
    for _, code, start in _session_specs(event):
        if pd.isna(start) or start > now:
            continue
        suffixes = ("results", "laps") if code.startswith("FP") else ("results",)
        present = False
        for suffix in suffixes:
            path = RAW_DATA_DIR / f"{year}_{code.lower()}_{suffix}.parquet"
            if not path.exists():
                continue
            frame = pd.read_parquet(path, columns=["RoundNumber"])
            if "RoundNumber" in frame:
                rows = frame.loc[pd.to_numeric(frame["RoundNumber"], errors="coerce").eq(
                    int(event["RoundNumber"]))]
                if not rows.empty:
                    present = True
                    break
        if not present:
            missing.add(code)
    return missing


def collect_historical_data(year: int, event: str, events_per_season=DEFAULT_EVENTS_PER_SEASON,
                            similar_circuits=None) -> None:
    """Collect only a bounded, relevant set of weekends, reusing saved sessions."""
    if year < HISTORICAL_START_YEAR:
        raise ValueError(f"Target year must be {HISTORICAL_START_YEAR} or later.")
    if isinstance(events_per_season, bool) or not isinstance(events_per_season, int) or events_per_season < 1:
        raise ValueError("events_per_season must be a positive integer")
    RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)
    target_schedule = fastf1.get_event_schedule(year, include_testing=False)
    target = _resolve_event(target_schedule, year, event)
    if not similar_circuit_keys(target, similar_circuits):
        logger.warning("No similar-circuit preset/override for %s; using the target and preceding events.", event)
    failures = []
    for collection_year in range(HISTORICAL_START_YEAR, year + 1):
        try:
            schedule = (target_schedule if collection_year == year else
                        fastf1.get_event_schedule(collection_year, include_testing=False))
            schedule = schedule.loc[pd.to_numeric(schedule["RoundNumber"], errors="coerce").gt(0)]
            schedule.assign(Year=collection_year).to_parquet(
                RAW_DATA_DIR / f"{collection_year}_schedule.parquet", index=False)
            selected = select_related_events(schedule, target, collection_year, year,
                                             events_per_season, similar_circuits)
            logger.info("Collection plan %s (%s/%s events): %s", collection_year, len(selected),
                        events_per_season, ", ".join(selected.EventName.astype(str)))
            for _, scheduled_event in selected.iterrows():
                missing = _missing_sessions(collection_year, scheduled_event)
                if not missing:
                    continue
                try:
                    collect_event(collection_year, scheduled_event["EventName"],
                                  event=scheduled_event, raise_on_failure=True,
                                  session_types=missing)
                    still_missing = _missing_sessions(collection_year, scheduled_event)
                    if still_missing:
                        failures.append(f"{collection_year} {scheduled_event['EventName']}: {sorted(still_missing)}")
                except Exception as exc:
                    if "does not exist for this event" in str(exc).lower():
                        logger.warning("Ignoring non-existent session error for %s %s: %s",
                                       collection_year, scheduled_event['EventName'], exc)
                        continue
                    failures.append(f"{collection_year} {scheduled_event['EventName']}: {exc}")
        except Exception as exc:
            failures.append(f"{collection_year} schedule: {exc}")
    collect_driver_constructor_stats()
    if failures:
        raise RuntimeError("Historical collection incomplete; retry missing sessions: " + "; ".join(failures))
    logger.info("Bounded historical collection complete; other calendar events were not downloaded.")


def _data_hash(paths, context=""):
    digest = hashlib.sha256(context.encode())
    for path in sorted(paths, key=str):
        digest.update(str(path).encode())
        if path.suffix == ".py":
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
        else:
            stat = path.stat()
            digest.update(f":{stat.st_size}:{stat.st_mtime_ns}".encode())
    return digest.hexdigest()


def _load_feature_inputs():
    inputs = {}
    for key in ("laps", "weather", "messages"):
        frames = [pd.read_parquet(path) for path in sorted(RAW_DATA_DIR.glob(f"*_{key}.parquet"))]
        inputs[key] = pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()
    news_paths = list(RAW_DATA_DIR.glob("*news*.parquet"))
    inputs["news"] = (pd.concat([pd.read_parquet(path) for path in news_paths], ignore_index=True)
                      if news_paths else pd.DataFrame())
    news_csv = RAW_DATA_DIR / "news.csv"
    if news_csv.exists():
        inputs["news"] = pd.concat([inputs["news"], pd.read_csv(news_csv)], ignore_index=True)
    return inputs


def _add_scheduled_target(results, session, year, event):
    """Use confirmed weekend entrants and scheduled starts, never invented outcomes."""
    path = RAW_DATA_DIR / f"{year}_schedule.parquet"
    if not path.exists():
        return results
    scheduled = _resolve_event(pd.read_parquet(path), year, event)
    code = SESSION_TYPES[session][0]
    this_event = (pd.to_numeric(results.Year, errors="coerce").eq(year)
                  & pd.to_numeric(results.RoundNumber, errors="coerce").eq(int(scheduled.RoundNumber)))
    if (this_event & results.SessionType.eq(code)).any():
        return results
    starts = [start for _, kind, start in _session_specs(scheduled) if kind == code and pd.notna(start)]
    if not starts:
        raise RuntimeError(f"No scheduled start for {event} {session}.")
    start = starts[0]
    entrants = results.loc[this_event & (pd.to_datetime(results.SessionStartUtc, utc=True) < start)]
    if entrants.empty:
        raise RuntimeError(f"No confirmed weekend entry list for {event}; collect entrants before predicting.")
    # Reserve/practice-only drivers are not confirmed race entrants. Prefer Q/SQ or S.
    confirmed = entrants.loc[entrants.SessionType.isin(["Q", "SQ", "S"])]
    if confirmed.empty:
        raise RuntimeError(f"No confirmed qualifying/sprint entrants for {event}; supply a verified entry list.")
    confirmed = confirmed.loc[confirmed.SessionStartUtc.eq(confirmed.SessionStartUtc.max())]
    latest = confirmed.sort_values("SessionStartUtc").drop_duplicates("Abbreviation", keep="last").copy()
    latest["SessionType"] = code
    latest["SessionStartUtc"] = start
    for column in ("Position", "Points", "Status", "ClassifiedPosition", "GridPosition", "GridPublishedAt",
                   "Q1", "Q2", "Q3"):
        if column in latest:
            latest[column] = pd.NaT if column == "GridPublishedAt" else np.nan
    return pd.concat([results, latest], ignore_index=True, sort=False)


def ensure_processed_data(session: str, year: int, event: str, stage="post_quali",
                          skip_collection=False, events_per_season=DEFAULT_EVENTS_PER_SEASON,
                          similar_circuits=None) -> Path:
    """Cache staged features using raw file metadata and source code content."""
    if session not in SESSION_TYPES or stage not in STAGES:
        raise ValueError("Unsupported session or prediction stage.")
    if year < HISTORICAL_START_YEAR:
        raise ValueError(f"Target year must be {HISTORICAL_START_YEAR} or later.")
    PROCESSED_DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not skip_collection:
        collect_historical_data(year, event, events_per_season, similar_circuits)
    session_code, feature_name = SESSION_TYPES[session]
    source_paths = list(RAW_DATA_DIR.glob("*.parquet"))
    source_paths.extend(RAW_DATA_DIR.glob("*news*.csv"))
    source_paths.extend([Path("src/features/engineer_features.py"),
                         Path("src/data/normalise_entities.py"), Path("src/data/outcomes.py"),
                         Path("src/utils/config.py"), Path("main.py")])
    fingerprint = _data_hash(source_paths, f"{session}:{year}:{event}:{stage}")
    feature_path = PROCESSED_DATA_DIR / f"{feature_name}_{stage}_features.parquet"
    hash_path = feature_path.with_suffix(".sha256")
    if feature_path.exists() and hash_path.exists() and hash_path.read_text() == fingerprint:
        return feature_path
    results = load_results()
    result_years = pd.to_numeric(results["Year"], errors="coerce")
    historical_results = results.loc[result_years.between(HISTORICAL_START_YEAR, year)].copy()
    historical_results = _add_scheduled_target(historical_results, session, year, event)
    features = build_features(historical_results, stage=stage, **_load_feature_inputs())
    session_features = features.get(session_code)
    if session_features is None or session_features.empty:
        raise RuntimeError(f"No {session} features available for {HISTORICAL_START_YEAR}-{year}.")
    session_features.to_parquet(feature_path, index=False)
    # Keep the canonical filename available to existing analysis pages.
    session_features.to_parquet(PROCESSED_DATA_DIR / f"{feature_name}_features.parquet", index=False)
    hash_path.write_text(fingerprint)
    return feature_path


def _resolve_target_event(year: int, event: str):
    path = RAW_DATA_DIR / f"{year}_schedule.parquet"
    if not path.exists():
        raise RuntimeError(f"No event schedule is available for {year}; unable to resolve {event!r}.")
    try:
        row = _resolve_event(pd.read_parquet(path), year, event)
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    return int(row["RoundNumber"]), str(row["EventName"])


def _select_target_event(features, year, round_number, event_name, session):
    selected = features.loc[pd.to_numeric(features["Year"], errors="coerce").eq(year)
                            & pd.to_numeric(features["RoundNumber"], errors="coerce").eq(round_number)].copy()
    if "SessionType" in selected:
        selected = selected.loc[selected["SessionType"].eq(SESSION_TYPES[session][0])].copy()
    if selected.empty:
        raise RuntimeError(f"No {session} session data found for {event_name} in {year} (round {round_number}).")
    return selected


def _validate_completed_target_results(target_event_df):
    columns = ["target_winner", "target_podium", "target_position"]
    if any(column not in target_event_df for column in columns):
        raise ValueError("Variation measurement requires completed target results; missing target columns.")
    targets = target_event_df[columns].apply(pd.to_numeric, errors="coerce")
    if targets.isna().any().any() or not np.isfinite(targets.to_numpy()).all():
        raise ValueError("Variation measurement requires completed target results; missing outcomes.")
    if not targets.target_winner.isin([0, 1]).all() or targets.target_winner.sum() != 1:
        raise ValueError("Variation measurement requires exactly one completed event winner.")
    if not targets.target_podium.isin([0, 1]).all() or (targets.target_position <= 0).any():
        raise ValueError("Variation measurement requires valid completed finishing positions and podium outcomes.")


def _training_before(features, target):
    if "SessionStartUtc" not in features:
        raise ValueError("SessionStartUtc is required for chronological training.")
    starts = pd.to_datetime(features["SessionStartUtc"], utc=True, errors="coerce")
    target_starts = pd.to_datetime(target["SessionStartUtc"], utc=True, errors="coerce")
    if target_starts.isna().any():
        raise ValueError("Target session has no valid SessionStartUtc cutoff.")
    cutoff = target_starts.min()
    if "FeatureCutoffUtc" in target:
        cutoffs = pd.to_datetime(target.FeatureCutoffUtc, utc=True, errors="coerce")
        if cutoffs.isna().any():
            raise ValueError("Target session has no valid FeatureCutoffUtc.")
        cutoff = min(cutoff, cutoffs.min())
    training = features.loc[starts < cutoff].copy()
    training = training.loc[pd.to_numeric(training["target_position"], errors="coerce").gt(0)]
    if training.empty or training["target_winner"].dropna().nunique() < 2:
        raise ValueError("Not enough historical winner examples before the target session.")
    return training


def _prepare_backtest_data(features, year, round_number, event_name, session):
    held_out = _select_target_event(features, year, round_number, event_name, session)
    _validate_completed_target_results(held_out)
    return _training_before(features, held_out), held_out


def _measure_prediction_variation(predictions, actuals):
    columns = {"target_winner": "actual_winner", "target_podium": "actual_podium",
               "target_position": "actual_position"}
    actual_results = actuals[["Abbreviation", *columns]].rename(columns=columns)
    measured = predictions.merge(actual_results, on="Abbreviation", how="left", validate="one_to_one")
    if measured[list(columns.values())].isna().any().any():
        raise ValueError("Could not align all held-out predictions with their completed results.")
    for probability, actual in (("win_probability", "actual_winner"), ("podium_probability", "actual_podium")):
        measured[f"{probability}_error"] = measured[probability] - measured[actual]
        measured[f"{probability}_absolute_error"] = measured[f"{probability}_error"].abs()
    measured["position_error"] = measured.expected_position - measured.actual_position
    measured["absolute_position_error"] = measured.position_error.abs()
    summary = {
        "winner_top1_correct": int(measured.loc[measured.win_probability.idxmax(), "actual_winner"] == 1),
        "win_probability_brier": float((measured.win_probability_error ** 2).mean()),
        "podium_probability_brier": float((measured.podium_probability_error ** 2).mean()),
        "position_mae": float(measured.absolute_position_error.mean()),
        "position_median_absolute_error": float(measured.absolute_position_error.median()),
        "position_error_std": float(measured.position_error.std(ddof=0)),
    }
    return measured, summary


def _training_hash(training, strength_model="legacy"):
    values = training.sort_index(axis=1).astype(str)
    data = pd.util.hash_pandas_object(values, index=False).to_numpy().tobytes()
    paths = [*Path("src/models").glob("*.py"), *Path("src/simulation").glob("*.py"),
             Path("src/utils/config.py"), Path("src/data/outcomes.py")]
    context = hashlib.sha256(data).hexdigest()
    if strength_model != "legacy":
        context += ":" + strength_model
    return _data_hash(paths, context)


def _train_models(feature_path, training_features=None, output_dir=None, strength_model="legacy"):
    """Train a single race model; temporary artifacts never enter models/."""
    from src.models.race_model import train_and_save_race_model

    options = {"strength_model": strength_model} if strength_model != "legacy" else {}
    if training_features is None:
        return train_and_save_race_model(feature_path, output_dir=output_dir, **options)
    with tempfile.TemporaryDirectory(prefix="backtest_", dir=PROCESSED_DATA_DIR) as directory:
        temporary = Path(directory)
        training_path = temporary / Path(feature_path).name
        training_features.to_parquet(training_path, index=False)
        artifacts = train_and_save_race_model(training_path, output_dir=temporary, **options)
        # Load before the temporary directory is removed, with no production writes.
        return joblib.load(artifacts["model_path"])


def _model_artifact(feature_path, training, stage, session, action, model_path=None, strength_model="legacy"):
    fingerprint = _training_hash(training, strength_model)
    if model_path is not None:
        artifact = joblib.load(model_path)
        metadata = artifact.get("metadata", {})
        if metadata.get("training_hash") != fingerprint:
            raise ValueError("Model data/code fingerprint differs from current chronological training data; retrain it.")
        if metadata.get("stage") != stage or metadata.get("session") != session:
            raise ValueError("Model stage/session does not match the requested forecast.")
        return artifact
    candidates = sorted(MODELS_DIR.glob("*/metadata.json"), reverse=True) if MODELS_DIR.exists() else []
    if action != "train":
        for path in candidates:
            metadata = json.loads(path.read_text())
            if (metadata.get("training_hash") == fingerprint and metadata.get("stage") == stage
                    and metadata.get("session") == session):
                model_file = path.parent / metadata["artifact_filename"]
                if model_file.exists():
                    return joblib.load(model_file)
        if action == "predict":
            raise RuntimeError("No matching trained model. Run the train command first or use the legacy automatic command.")
    from src.models.race_model import train_and_save_race_model

    run_id = pd.Timestamp.now(tz="UTC").strftime("%Y%m%dT%H%M%S%fZ") + "_" + fingerprint[:8]
    output_dir = MODELS_DIR / run_id
    output_dir.mkdir(parents=True, exist_ok=False)
    training_path = output_dir / "training_features.parquet"
    training.to_parquet(training_path, index=False)
    options = {"strength_model": strength_model} if strength_model != "legacy" else {}
    artifacts = train_and_save_race_model(training_path, output_dir=output_dir, **options)
    artifact = joblib.load(artifacts["model_path"])
    try:
        git_sha = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                                 text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        git_sha = "unknown"
    metadata = dict(artifact.get("metadata", {}))
    metadata.update({"model_version": run_id, "training_hash": fingerprint,
                     "stage": stage, "session": session, "git_sha": git_sha,
                     "training_start_utc": str(training.SessionStartUtc.min()),
                     "training_end_utc": str(training.SessionStartUtc.max()),
                     "training_races": int(training[["Year", "RoundNumber"]].drop_duplicates().shape[0]),
                     "training_rows": len(training), "feature_columns": artifact.get("feature_columns", []),
                     "artifact_filename": Path(artifacts["model_path"]).name})
    artifact["metadata"] = metadata
    joblib.dump(artifact, artifacts["model_path"])
    (output_dir / "metadata.json").write_text(json.dumps(metadata, indent=2, default=str))
    return artifact


def _print_predictions(predictions, session):
    columns = ["Abbreviation", "DriverName", "TeamName", "win_probability", "podium_probability",
               "top10_probability", "expected_position", "median_position", "p10_position",
               "p90_position", "p10_finish", "p90_finish", "dnf_probability", "vs_grid_order"]
    print("\n" + "=" * 70)
    print(f"{session.upper()} PREDICTIONS")
    print("=" * 70)
    print(predictions[[column for column in columns if column in predictions]].to_string(
        index=False, float_format=lambda value: f"{value:.3f}"))
    print("=" * 70)


def _validate_prediction_probabilities(predictions):
    columns = ["win_probability", "podium_probability", "top10_probability"]
    probabilities = predictions[columns].to_numpy(dtype=float)
    if (not len(predictions) or not np.isfinite(probabilities).all()
            or (probabilities < 0).any() or (probabilities > 1).any()):
        raise ValueError("Prediction probabilities must be finite values in [0, 1].")
    totals = probabilities.sum(axis=0)
    expected = [1, min(3, len(predictions)), min(10, len(predictions))]
    if not np.allclose(totals, expected, rtol=0, atol=1e-9):
        raise ValueError("Inconsistent win, podium or top-10 probability totals.")
    if (np.diff(probabilities, axis=1) < -1e-12).any():
        raise ValueError("Predictions must satisfy win <= podium <= top10.")


def _evaluate(features, session, year, target_round, stage, seed=42, n_simulations=20000,
              strength_model="legacy"):
    from src.models.evaluate import run_race_walk_forward

    keys = ["Year", "RoundNumber"]
    observed = pd.to_numeric(features.target_position, errors="coerce").notna()
    groups = observed.groupby([features[column] for column in keys])
    complete = groups.transform("all")
    if (groups.transform("any") & ~complete).any():
        raise ValueError("Partially observed event classification; cannot evaluate an incomplete field.")
    if (~complete).any():
        logger.info("Excluding %s events with unobserved classifications from evaluation.",
                    len(features.loc[~complete, keys].drop_duplicates()))
        features = features.loc[complete].copy()
    if len(features[keys].drop_duplicates()) < 9:
        raise ValueError("Walk-forward evaluation needs at least nine completed events (eight training, one held out).")
    options = {"strength_model": strength_model} if strength_model != "legacy" else {}
    result = run_race_walk_forward(features, seed=seed, n_simulations=n_simulations, **options)
    if result["metrics"].empty:
        raise ValueError("Walk-forward evaluation needs at least nine completed events (eight training, one held out).")
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    prefix = f"walk_forward_{session}_{year}_rnd{target_round}_{stage}"
    if strength_model != "legacy":
        prefix += "_" + strength_model
    for name, value in result.items():
        if isinstance(value, pd.DataFrame):
            value.to_parquet(REPORTS_DIR / f"{prefix}_{name}.parquet", index=False)
    for name in ("summary", "confidence", "reliability"):
        if name in result:
            print(f"\n{name.upper()}\n{result[name].to_string(index=False)}")
    return result


def run_pipeline(session, year, event, measure_variation=False, *, stage="post_quali",
                 skip_collection=False, action="auto", model_path=None, n_simulations=20000,
                 seed=42, scenario="standard_dry", events_per_season=DEFAULT_EVENTS_PER_SEASON,
                 similar_circuits=None, strength_model="legacy"):
    from src.models.race_model import STRENGTH_MODELS

    if strength_model not in STRENGTH_MODELS:
        raise ValueError(f"Unknown strength model {strength_model!r}; choose {STRENGTH_MODELS}")
    if n_simulations <= 0:
        raise ValueError("n_simulations must be positive.")
    feature_path = ensure_processed_data(session, year, event, stage, skip_collection,
                                        events_per_season, similar_circuits)
    features = pd.read_parquet(feature_path)
    valid, errors = validate_features_df(features)
    if not valid:
        raise ValueError("Feature validation failed: " + "; ".join(errors))
    target_round, event_name = _resolve_target_event(year, event)
    target = _select_target_event(features, year, target_round, event_name, session)
    if action == "evaluate":
        cutoff = pd.to_datetime(target.SessionStartUtc, utc=True).min()
        return _evaluate(features.loc[pd.to_datetime(features.SessionStartUtc, utc=True) <= cutoff],
                         session, year, target_round, stage, seed, n_simulations, strength_model)
    if measure_variation:
        training, target = _prepare_backtest_data(features, year, target_round, event_name, session)
        PROCESSED_DATA_DIR.mkdir(parents=True, exist_ok=True)
        artifact = _train_models(feature_path, training, strength_model=strength_model)
    else:
        training = _training_before(features, target)
        artifact = _model_artifact(feature_path, training, stage, session, action, model_path, strength_model)
    if action == "train":
        logger.info("Model trained: %s", artifact.get("metadata", {}).get("model_version"))
        return artifact
    predictions = artifact["model"].predict(target, n_simulations=n_simulations, scenario=scenario, seed=seed)
    predictions = predictions.copy()
    _validate_prediction_probabilities(predictions)
    if "DriverName" not in predictions:
        predictions["DriverName"] = predictions.Abbreviation.map(normalise_driver_name)
    predictions = predictions.sort_values("win_probability", ascending=False).reset_index(drop=True)
    if "grid_position" in target:
        grids = target.set_index("Abbreviation").grid_position
        predictions["vs_grid_order"] = predictions.Abbreviation.map(grids) - predictions.expected_position
    metadata = artifact.get("metadata", {})
    run_metadata = {"session": session, "year": year, "round_number": target_round,
                    "event_name": event_name, "prediction_generated_at_utc": pd.Timestamp.now(tz="UTC"),
                    "variation_measured": measure_variation, "stage": stage,
                    "strength_model": strength_model,
                    "model_version": metadata.get("model_version", "held_out_" + _training_hash(training)[:12]),
                    "training_start_utc": str(training.SessionStartUtc.min()),
                    "training_end_utc": str(training.SessionStartUtc.max()),
                    "training_races": len(training[["Year", "RoundNumber"]].drop_duplicates()),
                    "scenario": scenario, "simulation_count": n_simulations, "seed": seed,
                    "events_per_season_cap": events_per_season,
                    "history_scope": "available_collected_sessions_not_full_championship"}
    summary = None
    if measure_variation:
        predictions, summary = _measure_prediction_variation(predictions, target)
    for name, value in run_metadata.items():
        predictions[name] = value
    PREDICTIONS_DATA_DIR.mkdir(parents=True, exist_ok=True)
    stem = f"{session}_{year}_rnd{target_round}"
    if strength_model != "legacy":
        stem += "_" + strength_model
    predictions.to_parquet(PREDICTIONS_DATA_DIR / f"predictions_{stem}_{stage}.parquet", index=False)
    predictions.to_parquet(PREDICTIONS_DATA_DIR / f"predictions_{stem}.parquet", index=False)
    if summary is not None:
        pd.DataFrame([{**run_metadata, **summary}]).to_parquet(
            PREDICTIONS_DATA_DIR / f"variation_{stem}_{stage}.parquet", index=False)
        pd.DataFrame([{**run_metadata, **summary}]).to_parquet(
            PREDICTIONS_DATA_DIR / f"variation_{stem}.parquet", index=False)
    _print_predictions(predictions, session)
    if summary is not None:
        print("\nHELD-OUT ERRORS (separate from predictions)\n" + pd.DataFrame([summary]).to_string(index=False))
    print(f"\nDerived forecast: {scenario}; stage={stage}; samples={n_simulations:,}; seed={seed}")
    return predictions


def main(argv=None):
    from src.models.race_model import DEFAULT_STRENGTH_MODEL, STRENGTH_MODELS

    arguments = list(sys.argv[1:] if argv is None else argv)
    action = arguments.pop(0) if arguments and arguments[0] in {"train", "predict", "evaluate"} else "auto"
    parser = argparse.ArgumentParser(description="Train, predict or evaluate an F1 session")
    parser.add_argument("session", choices=SESSION_TYPES)
    parser.add_argument("year", type=int)
    parser.add_argument("event")
    parser.add_argument("--measure-variation", action="store_true", help="Chronologically backtest a completed event without writing models/")
    parser.add_argument("--stage", choices=STAGES, default="post_quali")
    parser.add_argument("--skip-collection", action="store_true")
    parser.add_argument("--model-path", type=Path)
    parser.add_argument("--strength-model", choices=STRENGTH_MODELS, default=DEFAULT_STRENGTH_MODEL,
                        help="Competitive-strength model; legacy retains the original ranker/logit blend")
    parser.add_argument("--n-simulations", type=int, default=20000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--scenario", default="standard_dry")
    parser.add_argument("--events-per-season", type=int, default=DEFAULT_EVENTS_PER_SEASON,
                        help="Maximum collected weekends per season, including the target (default: 6)")
    parser.add_argument("--similar-circuits", nargs="*", default=None,
                        help="Override heuristic circuit presets; pass with no names to disable them")
    args = parser.parse_args(arguments)
    if args.year < HISTORICAL_START_YEAR:
        parser.error(f"year must be {HISTORICAL_START_YEAR} or later")
    if args.n_simulations <= 0:
        parser.error("--n-simulations must be positive")
    if args.events_per_season <= 0:
        parser.error("--events-per-season must be positive")
    if args.measure_variation and action in {"train", "evaluate"}:
        parser.error("--measure-variation is only valid for prediction")
    options = dict(stage=args.stage, skip_collection=args.skip_collection, action=action,
                   model_path=args.model_path, n_simulations=args.n_simulations, seed=args.seed,
                   scenario=args.scenario, events_per_season=args.events_per_season,
                   similar_circuits=args.similar_circuits, strength_model=args.strength_model)
    return run_pipeline(args.session, args.year, args.event, measure_variation=args.measure_variation, **options)


if __name__ == "__main__":
    main()
