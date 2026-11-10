"""Data validation and contract checks for raw and processed datasets."""

from typing import Dict, List, Tuple
import pandas as pd
from src.utils.config import NON_FEATURE_COLUMNS
from src.utils.logging import setup_logger

logger = setup_logger(__name__)


def validate_results_df(df: pd.DataFrame) -> Tuple[bool, List[str]]:
    """Validate a results dataframe for required columns and data integrity."""
    errors = []
    if df is None or df.empty:
        return False, ["Results dataframe is empty or None"]

    required_cols = ["Year", "RoundNumber", "SessionType", "Abbreviation", "Position"]
    for col in required_cols:
        if col not in df.columns:
            errors.append(f"Missing required column: {col}")

    if "Year" in df.columns and (df["Year"] < 1950).any():
        errors.append("Invalid Year values detected (< 1950)")

    if "Position" in df.columns and (df["Position"] < 1).any():
        errors.append("Invalid Position values detected (< 1)")

    # Check for duplicate driver entries in the same session
    if all(c in df.columns for c in ["Year", "RoundNumber", "SessionType", "Abbreviation"]):
        duplicates = df.duplicated(subset=["Year", "RoundNumber", "SessionType", "Abbreviation"]).sum()
        if duplicates > 0:
            errors.append(f"Found {duplicates} duplicate driver entries in identical sessions")

    return len(errors) == 0, errors


def validate_features_df(df: pd.DataFrame, session_type: str = "R") -> Tuple[bool, List[str]]:
    """Validate a processed feature table ensuring point-in-time compliance and schema validity."""
    errors = []
    if df is None or df.empty:
        return False, [f"Feature dataframe for {session_type} is empty or None"]

    for col in ["Year", "RoundNumber", "SessionStartUtc", "Abbreviation", "TeamName"]:
        if col not in df.columns:
            errors.append(f"Missing essential identifier column: {col}")

    if session_type == "R":
        for col in ["target_winner", "target_podium", "target_position"]:
            if col not in df.columns:
                errors.append(f"Missing target label column for race: {col}")

    # Check point-in-time timestamp integrity
    if "SessionStartUtc" in df.columns:
        null_starts = len(df.loc[df["SessionStartUtc"].isna()])
        if null_starts > 0:
            errors.append(f"Found {null_starts} rows with missing SessionStartUtc")

    # Check that there is at most one winner per race for classified races
    if "target_winner" in df.columns and all(c in df.columns for c in ["Year", "RoundNumber"]):
        winners_per_race = df.groupby(["Year", "RoundNumber"])["target_winner"].sum()
        multi_winners = len(winners_per_race.loc[winners_per_race > 1])
        if multi_winners > 0:
            errors.append(f"Found {multi_winners} races with multiple target winners")

    for column in df.columns:
        if column not in NON_FEATURE_COLUMNS and not column.startswith("target_"):
            if not pd.api.types.is_numeric_dtype(df[column]):
                errors.append(f"Non-numeric model feature: {column}")
            elif ("avg_position" in column or "_form_" in column) and not column.endswith("_is_missing"):
                if (pd.to_numeric(df[column], errors="coerce") <= 0).any():
                    errors.append(f"Nonpositive position feature: {column}")

    if "stage" in df.columns:
        if not df["stage"].isin(["pre_weekend", "post_fp", "post_quali"]).all():
            errors.append("Unknown feature stage")
        if "FeatureCutoffUtc" not in df.columns:
            errors.append("Stage features require FeatureCutoffUtc")
        elif "SessionStartUtc" in df.columns:
            cutoff = pd.to_datetime(df["FeatureCutoffUtc"], utc=True, errors="coerce")
            start = pd.to_datetime(df["SessionStartUtc"], utc=True, errors="coerce")
            if (cutoff.isna() | cutoff.gt(start)).any():
                errors.append("Invalid stage cutoff")
            for column in ("history_source_max_utc", "weekend_source_max_utc", "grid_source_max_utc", "news_source_max_utc"):
                if column not in df.columns:
                    errors.append(f"Missing stage provenance: {column}")
                    continue
                source = pd.to_datetime(df[column], utc=True, errors="coerce")
                if (df[column].notna() & source.isna()).any():
                    errors.append(f"Invalid source timestamp: {column}")
                if (source.notna() & source.ge(cutoff)).any():
                    errors.append(f"Point-in-time violation: {column} must precede cutoff")
            if "weekend_source_session_types" in df.columns:
                for stage, types in zip(df["stage"], df["weekend_source_session_types"].fillna("")):
                    sessions = set(filter(None, str(types).split(",")))
                    if stage == "pre_weekend" and sessions:
                        errors.append("pre_weekend contains weekend session sources")
                        break
                    if stage == "post_fp" and not sessions.issubset({"FP1", "FP2", "FP3"}):
                        errors.append("post_fp contains non-practice session sources")
                        break
            for column in ("grid_position", "quali_position", "quali_gap_to_pole_pct", "quali_gap_to_teammate_pct",
                           "grid_penalty_places", "pit_lane_start"):
                if column not in df.columns:
                    continue
                present = df[column].notna()
                flag = column + "_is_missing"
                if flag in df.columns:
                    present &= df[flag].eq(0)
                if (present & df["stage"].ne("post_quali")).any():
                    errors.append(f"Stage-ineligible qualifying/grid feature: {column}")
            weekend_columns = [column for column in df.columns if isinstance(column, str) and column.startswith("prior_weekend_")
                               and not column.endswith("_is_missing")]
            weekend_columns += [column for column in ("fp_best_lap_gap_pct", "fp2_long_run_pace_rank", "fp_teammate_gap_pct") if column in df]
            for column in weekend_columns:
                present = df[column].notna()
                if column + "_is_missing" in df.columns:
                    present &= df[column + "_is_missing"].eq(0)
                if (present & df["stage"].eq("pre_weekend")).any():
                    errors.append(f"Stage-ineligible weekend feature: {column}")
            provenance = {
                "grid_source_max_utc": ["grid_position", "pit_lane_start"],
                "weekend_source_max_utc": weekend_columns + ["quali_position", "quali_gap_to_pole_pct", "quali_gap_to_teammate_pct"],
                "news_source_max_utc": ["prior_news_item_count"],
            }
            for source_column, columns in provenance.items():
                if source_column not in df.columns:
                    continue
                for column in columns:
                    if column not in df.columns:
                        continue
                    present = df[column].notna()
                    if column + "_is_missing" in df.columns:
                        present &= df[column + "_is_missing"].eq(0)
                    # A zero news count legitimately has no latest publication.
                    if column == "prior_news_item_count":
                        present &= df[column].gt(0)
                    if (present & df[source_column].isna()).any():
                        errors.append(f"Missing provenance for populated feature: {column}")

    return len(errors) == 0, errors


def generate_quality_report(features_dict: Dict[str, pd.DataFrame]) -> Dict[str, dict]:
    """Generate summary data quality metrics across all feature tables."""
    report = {}
    for stype, df in features_dict.items():
        if df is None or df.empty:
            report[stype] = {"status": "EMPTY", "rows": 0, "cols": 0}
            continue
        is_valid, errors = validate_features_df(df, session_type=stype)
        null_pct = df.isnull().mean().to_dict()
        top_nulls = {k: round(v * 100, 1) for k, v in null_pct.items() if v > 0.3}
        report[stype] = {
            "status": "VALID" if is_valid else "INVALID",
            "rows": len(df),
            "columns": len(df.columns),
            "errors": errors,
            "high_null_features": top_nulls,
        }
    return report
