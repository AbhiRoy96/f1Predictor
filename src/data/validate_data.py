"""Data validation and contract checks for raw and processed datasets."""

from typing import Dict, List, Tuple
import pandas as pd
from src.utils.config import METADATA_COLUMNS, TARGET_COLUMNS
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
        null_starts = df["SessionStartUtc"].isna().sum()
        if null_starts > 0:
            errors.append(f"Found {null_starts} rows with missing SessionStartUtc")

    # Check that there is at most one winner per race for classified races
    if "target_winner" in df.columns and all(c in df.columns for c in ["Year", "RoundNumber"]):
        winners_per_race = df.groupby(["Year", "RoundNumber"])["target_winner"].sum()
        multi_winners = (winners_per_race > 1).sum()
        if multi_winners > 0:
            errors.append(f"Found {multi_winners} races with multiple target winners")

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
