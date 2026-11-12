"""Offline, explicit validation-window selection and once-only final testing.

The local lock is an accidental-reuse guard, not a tamper-proof experiment store.
No command collects data or promotes a model to production.
"""

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd
import numpy as np

WINDOWS = (12, 18, 24, 36, None)


def training_window(frame, cutoff, months=None):
    cutoff = pd.to_datetime(cutoff, utc=True, errors="raise")
    starts = pd.to_datetime(frame.SessionStartUtc, utc=True, errors="raise")
    mask = starts < cutoff
    if months is not None:
        if isinstance(months, bool) or months not in WINDOWS:
            raise ValueError("Training window must be 12, 18, 24, 36 months or all")
        mask &= starts >= cutoff - pd.DateOffset(months=months)
    return frame.loc[mask].copy()


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def code_hash():
    root = Path(__file__).resolve().parents[2]
    paths = [*root.joinpath("src", "models").glob("*.py"),
             *root.joinpath("src", "simulation").glob("*.py"),
             root / "src" / "features" / "engineer_features.py",
             root / "src" / "data" / "outcomes.py", root / "src" / "utils" / "config.py"]
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _write_new(path, document):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(document, stream, indent=2, allow_nan=False)


def lock_experiment(features, lock_path, cutoff, validation_start, *, seed=42,
                    n_simulations=2000, strength_model="ensemble", min_train_races=8):
    """Lock data, split and settings before any window comparison."""
    cutoff = pd.to_datetime(cutoff, utc=True, errors="raise")
    start = pd.to_datetime(validation_start, utc=True, errors="raise")
    if start >= cutoff or n_simulations < 1 or min_train_races < 2:
        raise ValueError("Validation must precede final cutoff; counts must be positive")
    if strength_model not in ("legacy", "bayesian", "ensemble"):
        raise ValueError("Unknown strength model")
    frame = pd.read_parquet(features)
    dates = pd.to_datetime(frame.SessionStartUtc, utc=True, errors="raise")
    for column in ("stage", "SessionType"):
        if column in frame and (frame[column].isna().any() or frame[column].nunique() != 1):
            raise ValueError("A locked experiment must contain exactly one stage and session type")
    if dates.isna().any() or not (dates >= cutoff).any() or not ((dates >= start) & (dates < cutoff)).any():
        raise ValueError("Lock requires both validation and final-test observations")
    from src.models.artifacts import library_versions
    document = {"schema": 1, "features": str(Path(features).resolve()),
                "features_sha256": file_hash(features), "cutoff": cutoff.isoformat(),
                "validation_start": start.isoformat(), "seed": seed,
                "n_simulations": n_simulations, "strength_model": strength_model,
                "min_train_races": min_train_races, "windows": list(WINDOWS),
                "library_versions": library_versions(), "status": "locked_not_evaluated"}
    document["code_sha256"] = code_hash()
    _write_new(lock_path, document)
    return document


def _load_lock(path):
    from src.models.artifacts import validate_library_versions
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_library_versions(document)
    if file_hash(document["features"]) != document["features_sha256"]:
        raise ValueError("Locked feature data changed; do not reuse this experiment")
    if code_hash() != document["code_sha256"]:
        raise ValueError("Locked model/evaluation code changed; do not reuse this experiment")
    return document, pd.read_parquet(document["features"])


def _options(lock):
    return {key: lock[key] for key in ("seed", "n_simulations", "strength_model", "min_train_races")}


def _save_tables(result, directory):
    for name, value in result.items():
        if isinstance(value, pd.DataFrame):
            value.to_parquet(directory / f"{name}.parquet", index=False)


def run_ablation(lock_path, output):
    from src.models.evaluate import run_race_walk_forward
    lock, frame = _load_lock(lock_path)
    if Path(str(lock_path) + ".final-consumed.json").exists():
        raise ValueError("Final test already consumed; no subsequent tuning is allowed")
    marker = Path(str(lock_path) + ".ablation.json")
    _write_new(marker, {"status": "started", "output": str(Path(output).resolve())})
    directory = Path(output)
    directory.mkdir(parents=True, exist_ok=False)
    frame = training_window(frame, lock["cutoff"])
    scores = []
    for months in WINDOWS:
        result = run_race_walk_forward(
            frame, window_months=months, evaluation_start=lock["validation_start"], **_options(lock))
        metrics = result["metrics"]
        model = metrics.loc[metrics.model.eq("race_model")] if not metrics.empty else pd.DataFrame()
        if len(model) < 5:
            raise ValueError("At least five shared validation races are required to select a window")
        # Use the same proper log-loss objective used by calibration.
        objectives = model[["win_log_loss", "podium_log_loss", "top10_log_loss", "conditional_top10_pl_nll"]]
        if not np.isfinite(objectives.to_numpy(float)).all():
            raise ValueError("Window selection requires finite held-out objectives and known finisher labels")
        score = float(objectives.sum(axis=1).mean())
        subdir = directory / ("all" if months is None else str(months))
        subdir.mkdir()
        _save_tables(result, subdir)
        scores.append({"window_months": months, "objective": score,
                       "race_indices": model.race_index.astype(int).tolist()})
    if any(row["race_indices"] != scores[0]["race_indices"] for row in scores):
        raise ValueError("Window ablation must use identical validation races")
    selected = min(scores, key=lambda row: row["objective"])["window_months"]
    selection = {"lock_sha256": file_hash(lock_path), "window_months": selected,
                 "scores": scores, "scope": "validation_only_no_final_test_evidence"}
    selection_path = directory / "selection.json"
    _write_new(selection_path, selection)
    marker.write_text(json.dumps({"status": "complete", "selection": str(selection_path.resolve()),
                                 "selection_sha256": file_hash(selection_path)}), encoding="utf-8")
    return selection


def run_final_test(lock_path, output):
    from src.models.evaluate import run_race_walk_forward
    lock, frame = _load_lock(lock_path)
    marker = json.loads(Path(str(lock_path) + ".ablation.json").read_text(encoding="utf-8"))
    if marker.get("status") != "complete" or file_hash(marker["selection"]) != marker["selection_sha256"]:
        raise ValueError("A completed, unchanged validation ablation is required")
    selected = json.loads(Path(marker["selection"]).read_text(encoding="utf-8"))
    if selected["lock_sha256"] != file_hash(lock_path):
        raise ValueError("Experiment lock changed after window selection")
    directory = Path(output)
    if directory.exists():
        raise FileExistsError(directory)
    # Reserve before evaluating; even a failed run consumes this final test.
    _write_new(str(lock_path) + ".final-consumed.json",
               {"status": "consumed", "output": str(directory.resolve()),
                "warning": "Do not tune or rerun against these final outcomes"})
    directory.mkdir(parents=True)
    result = run_race_walk_forward(
        frame, evaluation_start=lock["cutoff"], window_months=selected["window_months"],
        refit_every=len(frame) + 1, **_options(lock))
    if result["metrics"].empty:
        raise ValueError("No complete final-test races; reservation remains consumed")
    _save_tables(result, directory)
    evidence = {"status": "evaluated_once", "cutoff": lock["cutoff"],
                "window_months": selected["window_months"],
                "n_final_races": int(result["predictions"].race_index.nunique()),
                "baseline_selection": "past_only_oof_best_baseline",
                "claim": "No automatic skill or public-readiness claim; inspect paired intervals and DNF skill"}
    _write_new(directory / "evidence.json", evidence)
    return evidence


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    lock = commands.add_parser("lock")
    lock.add_argument("--features", type=Path, required=True)
    lock.add_argument("--lock", type=Path, required=True)
    lock.add_argument("--cutoff", required=True)
    lock.add_argument("--validation-start", required=True)
    lock.add_argument("--seed", type=int, default=42)
    lock.add_argument("--n-simulations", type=int, default=2000)
    lock.add_argument("--strength-model", choices=["legacy", "bayesian", "ensemble"], default="ensemble")
    lock.add_argument("--min-train-races", type=int, default=8)
    for name in ("ablate", "final-test"):
        command = commands.add_parser(name)
        command.add_argument("--lock", type=Path, required=True)
        command.add_argument("--output", type=Path, required=True)
    args = vars(parser.parse_args(argv))
    command = args.pop("command")
    if command == "lock":
        args["lock_path"] = args.pop("lock")
        result = lock_experiment(**args)
    else:
        result = (run_ablation if command == "ablate" else run_final_test)(args["lock"], args["output"])
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
