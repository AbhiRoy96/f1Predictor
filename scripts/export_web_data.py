"""Export saved forecasts and evaluation reports as JSON for the Next.js app.

Run from the repository root:
    python -m scripts.export_web_data            # saved runs in data/predictions
    python -m scripts.export_web_data --demo     # add a labelled illustrative sample
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from src.simulation.race_simulator import (
    MonteCarloRaceSimulator,
    simulate_saved_prediction,
)
from src.simulation.scenarios import SCENARIOS
from src.utils.config import PREDICTIONS_DATA_DIR, REPORTS_DIR
from src.utils.logging import setup_logger

logger = setup_logger(__name__)
DEFAULT_OUTPUT = Path("web/public/data")
DRIVER_COLUMNS = [
    "Abbreviation", "DriverName", "TeamName", "win_probability", "podium_probability",
    "top10_probability", "dnf_probability", "expected_position", "median_position",
    "p10_position", "p90_position", "p10_finish", "p90_finish", "vs_grid_order",
    "win_probability_mcse", "grid_position", "grid_is_quali_proxy",
]
META_COLUMNS = [
    "session", "year", "event_name", "round_number", "stage", "model_version", "strength_model",
    "prediction_generated_at_utc", "training_start_utc", "training_end_utc", "training_races",
    "scenario", "simulation_count", "seed", "pl_temperature", "history_scope", "calibration_races",
    "forecast_phase", "source_published_at_utc",
]
REPORT_NAMES = ("summary", "confidence", "reliability")
DEMO_FIELD = [
    ("NOR", "Lando Norris", "McLaren", 1.55, .06), ("PIA", "Oscar Piastri", "McLaren", 1.30, .06),
    ("RUS", "George Russell", "Mercedes", 1.25, .05), ("ANT", "Kimi Antonelli", "Mercedes", 1.10, .07),
    ("VER", "Max Verstappen", "Red Bull Racing", 1.20, .06), ("HAD", "Isack Hadjar", "Red Bull Racing", .55, .08),
    ("LEC", "Charles Leclerc", "Ferrari", .95, .07), ("HAM", "Lewis Hamilton", "Ferrari", .85, .07),
    ("ALB", "Alexander Albon", "Williams", .30, .07), ("SAI", "Carlos Sainz", "Williams", .25, .07),
    ("LAW", "Liam Lawson", "Racing Bulls", .05, .08), ("LIN", "Arvid Lindblad", "Racing Bulls", -.10, .10),
    ("ALO", "Fernando Alonso", "Aston Martin", -.15, .10), ("STR", "Lance Stroll", "Aston Martin", -.40, .11),
    ("OCO", "Esteban Ocon", "Haas", -.20, .09), ("BEA", "Oliver Bearman", "Haas", -.15, .09),
    ("HUL", "Nico Hulkenberg", "Audi", -.30, .10), ("BOR", "Gabriel Bortoleto", "Audi", -.45, .10),
    ("GAS", "Pierre Gasly", "Alpine", -.25, .09), ("COL", "Franco Colapinto", "Alpine", -.50, .10),
    ("PER", "Sergio Perez", "Cadillac", -.70, .11), ("BOT", "Valtteri Bottas", "Cadillac", -.80, .11),
]


def _records(frame: pd.DataFrame) -> list[dict]:
    return json.loads(frame.to_json(orient="records"))


def _scenario_payloads(frame: pd.DataFrame) -> dict[str, dict]:
    required = {"strength", "base_dnf_probability", "pl_temperature", "simulation_count", "seed",
                "race_retirement_correlation"}
    if not required.issubset(frame.columns) or frame.get("session", pd.Series(["race"])).iloc[0] == "qualifying":
        return {}
    payloads = {}
    columns = ["Abbreviation", "TeamName", "simulated_win_prob", "simulated_podium_prob",
               "simulated_dnf_prob", "expected_position", "top10_probability"]
    for key, scenario in SCENARIOS.items():
        result = simulate_saved_prediction(frame, key)
        payloads[key] = {
            "name": scenario.name, "description": scenario.description,
            "kind": "calibrated_reference" if key == "standard_dry" else "heuristic_sensitivity",
            "parameters": {"safetyCar": scenario.safety_car_probability, "vsc": scenario.vsc_probability,
                           "rain": scenario.weather_rain_probability, "dnf": scenario.dnf_base_rate,
                           "overtakingDifficulty": scenario.overtaking_difficulty},
            "drivers": _records(result[[c for c in columns if c in result]]),
        }
    return payloads


def _reports(meta: pd.Series, reports_dir: Path) -> dict[str, list[dict]]:
    required = ("session", "year", "round_number", "stage")
    if any(pd.isna(meta.get(column)) for column in required):
        return {}
    prefix = (f"walk_forward_{meta['session']}_{int(meta['year'])}_"
              f"rnd{int(meta['round_number'])}_{meta['stage']}")
    variant = meta.get("strength_model", "legacy")
    if variant in {"ensemble", "bayesian"}:
        prefix += f"_{variant}"
    found = {}
    for name in REPORT_NAMES:
        path = reports_dir / f"{prefix}_{name}.parquet"
        if path.exists():
            found[name] = _records(pd.read_parquet(path))
    return found


def build_run(frame: pd.DataFrame, run_id: str, reports: dict | None = None, demo: bool = False) -> dict:
    """Convert one saved forecast into the JSON contract consumed by the web app."""
    if frame.empty or "win_probability" not in frame:
        raise ValueError("Forecast requires nonempty rows with win_probability")
    frame = frame.sort_values("win_probability", ascending=False).reset_index(drop=True)
    position_columns = sorted((c for c in frame if isinstance(c, str) and c.startswith("prob_p")
                               and c[6:].isdigit()), key=lambda c: int(c[6:]))
    first = frame.iloc[0]
    drivers = _records(frame[[c for c in DRIVER_COLUMNS if c in frame]])
    for row, matrix_row in zip(drivers, frame[position_columns].to_numpy(float)):
        row["positionProbabilities"] = matrix_row.round(6).tolist()
    meta = {column: first[column] for column in META_COLUMNS if column in frame and pd.notna(first[column])}
    meta = json.loads(pd.Series(meta).to_json(date_format="iso"))
    meta.update({"id": run_id, "demo": demo})
    return {"meta": meta, "drivers": drivers, "scenarios": _scenario_payloads(frame),
            "reports": reports or {}}


def build_demo_frame(n_simulations: int = 20000, seed: int = 2026) -> pd.DataFrame:
    """Illustrative field built with the production simulator; not a forecast."""
    table = pd.DataFrame(DEMO_FIELD, columns=["Abbreviation", "DriverName", "TeamName", "strength", "base_dnf_probability"])
    # Saved forecasts are replayed in alphabetical order; simulate in that order so draws match exactly.
    table = table.sort_values("Abbreviation").reset_index(drop=True)
    table["grid_position"] = np.argsort(np.argsort(-table.strength.to_numpy() + np.random.default_rng(3).normal(0, .5, len(table)))) + 1
    result = MonteCarloRaceSimulator(n_simulations, seed).simulate(
        table, "standard_dry", strength=table.strength.to_numpy(), p_dnf=table.base_dnf_probability.to_numpy(),
        pl_temperature=1.0, race_correlation=.15)
    frame = result.drop(columns=["TeamName"]).merge(table, on="Abbreviation")
    return frame.assign(
        session="race", year=2026, event_name="São Paulo Grand Prix", round_number=np.nan,
        stage="post_quali", model_version="illustrative_sample", strength_model="illustrative",
        history_scope="not_applicable_illustrative", training_races=0, vs_grid_order=np.nan,
        prediction_generated_at_utc=pd.Timestamp.now(tz="UTC").isoformat())


def export(output: Path, predictions_dir: Path, reports_dir: Path, demo: bool) -> list[dict]:
    runs_dir = output / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    for stale in runs_dir.glob("*.json"):
        stale.unlink()
    manifest = []
    sources = [(path.stem.removeprefix("predictions_"), pd.read_parquet(path), False)
               for path in sorted(predictions_dir.glob("predictions_*.parquet"),
                                  key=lambda p: p.stat().st_mtime, reverse=True)]
    if demo:
        sources.append(("demo", build_demo_frame(), True))
    for run_id, frame, is_demo in sources:
        try:
            meta = frame.iloc[0] if not frame.empty else pd.Series(dtype=object)
            run = build_run(frame, run_id, {} if is_demo else _reports(meta, reports_dir), is_demo)
        except (ValueError, KeyError) as exc:
            logger.warning("Skipping %s: %s", run_id, exc)
            continue
        (runs_dir / f"{run_id}.json").write_text(json.dumps(run, allow_nan=False), encoding="utf-8")
        manifest.append({"id": run_id, **{k: run["meta"].get(k) for k in
                         ("event_name", "year", "session", "stage", "model_version", "demo",
                          "prediction_generated_at_utc")}})
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--predictions-dir", type=Path, default=PREDICTIONS_DATA_DIR)
    parser.add_argument("--reports-dir", type=Path, default=REPORTS_DIR)
    parser.add_argument("--demo", action="store_true", help="Append a labelled illustrative sample run")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = create_parser().parse_args(argv)
    manifest = export(args.output, args.predictions_dir, args.reports_dir, args.demo)
    logger.info("Exported %s run(s) to %s", len(manifest), args.output)
    if not manifest:
        logger.error("No runs exported; generate a forecast or pass --demo.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
