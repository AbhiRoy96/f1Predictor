"""Audit refreshed features and raw retirement statuses.

Run from the repository root: python -m scripts.audit_features
"""

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

from src.data.outcomes import dnf_labels
from src.utils.config import PROCESSED_DATA_DIR, RAW_DATA_DIR, REPORTS_DIR
from src.utils.logging import setup_logger

logger = setup_logger(__name__)
AUDIT_COLUMNS = [
    "grid_position", "quali_position", "quali_gap_to_pole_pct", "quali_gap_to_teammate_pct",
    "fp2_long_run_pace_rank", "fp_best_lap_gap_pct",
]


def audit_features(frame: pd.DataFrame, results: pd.DataFrame | None = None) -> dict[str, pd.DataFrame]:
    """Return feature coverage, latest entrants, per-race grid and DNF audits."""
    required = {"Year", "RoundNumber", "Abbreviation"}
    if not required.issubset(frame) or frame.empty:
        raise ValueError(f"Expected nonempty features with columns {sorted(required)}")
    frame = frame.loc[frame.SessionType.eq("R")].copy() if "SessionType" in frame else frame.copy()
    if frame.empty:
        raise ValueError("No race feature rows to audit")
    latest = frame.sort_values(["Year", "RoundNumber"]).iloc[-1]
    latest = frame.loc[frame.Year.eq(latest.Year) & frame.RoundNumber.eq(latest.RoundNumber)]
    issues, coverage, grids = [], [], []
    for column in AUDIT_COLUMNS:
        if column not in frame:
            issues.append(f"Missing feature column: {column}")
            continue
        values = pd.to_numeric(frame[column], errors="coerce")
        missing = values.isna()
        if column + "_is_missing" in frame:
            missing |= frame[column + "_is_missing"].eq(1)
        coverage.append({"feature": column, "distinct": values.mask(missing).nunique(),
                             "missing_fraction": missing.mean(),
                             "latest_distinct": values.loc[latest.index].mask(missing.loc[latest.index]).nunique()})
    for (year, round_number), race in frame.groupby(["Year", "RoundNumber"], sort=True):
        if "stage" in race and not race.stage.eq("post_quali").all():
            continue
        grid = pd.to_numeric(race.get("grid_position", pd.Series(float("nan"), index=race.index)), errors="coerce")
        if "grid_position_is_missing" in race:
            grid = grid.mask(race.grid_position_is_missing.eq(1))
        distinct = grid.nunique()
        grids.append({"Year": year, "RoundNumber": round_number, "drivers": len(race),
                          "grid_distinct": distinct, "grid_missing": int(grid.isna().sum())})
        if len(race) >= 18 and distinct <= 5:
            issues.append(f"Degenerate post-quali grid: {year} round {round_number} ({distinct} distinct)")
    if "grid_is_quali_proxy" in frame:
        proxy = frame.grid_is_quali_proxy.eq(1)
        if not frame.grid_is_quali_proxy.isin([0, 1]).all():
            issues.append("grid_is_quali_proxy must contain only 0 or 1")
        if proxy.any():
            if not {"grid_position", "quali_position"}.issubset(frame):
                issues.append("Qualifying proxies require grid_position and quali_position")
            elif not frame.loc[proxy, "grid_position"].eq(frame.loc[proxy, "quali_position"]).all():
                issues.append("Qualifying proxy differs from qualifying position")
    starts = next((c for c in ("driver_race_career_starts", "driver_race_career_sessions") if c in latest), None)
    latest_columns = ["Abbreviation", "TeamName", *AUDIT_COLUMNS, "grid_is_quali_proxy",
                      "grid_penalty_places", "pit_lane_start", "driver_race_form_5", "driver_race_ewma_position"]
    if starts:
        latest_columns.append(starts)
    latest_drivers = latest[[c for c in latest_columns if c in latest]].sort_values("Abbreviation").copy()
    if starts:
        latest_drivers["newcomer"] = latest_drivers[starts].lt(10)
    if results is not None:
        if "SessionType" not in results or "Status" not in results:
            raise ValueError("Raw results require SessionType and Status columns")
        outcomes = results.loc[results.SessionType.eq("R")]
        statuses = outcomes.Status.fillna("<missing>").value_counts(dropna=False).rename_axis("Status").reset_index(name="rows")
    else:
        outcomes = frame
        statuses = pd.DataFrame(columns=["Status", "rows"])
        issues.append("Raw results unavailable; Status distribution was not audited")
    total, mechanical, collision = dnf_labels(outcomes)
    labels = pd.DataFrame([{"rows": len(outcomes), "known": int(total.notna().sum()),
                               "unknown": int(total.isna().sum()), "retirements": int(total.eq(1).sum()),
                               "retirement_rate": total.mean(), "mechanical_known": int(mechanical.notna().sum()),
                               "collision_known": int(collision.notna().sum())}])
    return {
        "feature_summary": pd.DataFrame(coverage),
        "races_per_year": frame.groupby("Year").RoundNumber.nunique().reset_index(name="races"),
        "grid_by_race": pd.DataFrame(grids),
        "latest_drivers": latest_drivers,
        "dnf_status_counts": statuses,
        "dnf_labels": labels,
        "issues": pd.DataFrame({"issue": issues}),
    }


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--features", type=Path, default=PROCESSED_DATA_DIR / "race_post_quali_features.parquet")
    parser.add_argument("--raw-dir", type=Path, default=RAW_DATA_DIR)
    parser.add_argument("--output", type=Path, default=REPORTS_DIR / "feature_audit.json")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = create_parser().parse_args(argv)
    try:
        frame = pd.read_parquet(args.features)
        paths = sorted(args.raw_dir.glob("*_results.parquet"))
        results = pd.concat([pd.read_parquet(path) for path in paths], ignore_index=True) if paths else None
        report = audit_features(frame, results)
        for name, table in report.items():
            print(f"\n{name.upper()}\n{table.to_string(index=False, float_format=lambda value: f'{value:.3f}')}")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps({name: json.loads(table.to_json(orient="records"))
                                          for name, table in report.items()}, indent=2), encoding="utf-8")
        logger.info("Audit saved to %s", args.output)
        return 1 if not report["issues"].empty else 0
    except (OSError, ValueError, KeyError) as exc:
        logger.error("Feature audit failed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
