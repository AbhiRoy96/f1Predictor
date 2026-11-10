"""Configuration management for F1 Race Intelligence and Prediction."""

from pathlib import Path

# Base Paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data"
RAW_DATA_DIR = DATA_DIR / "raw"
PROCESSED_DATA_DIR = DATA_DIR / "processed"
TRAINING_DATA_DIR = DATA_DIR / "training"
PREDICTIONS_DATA_DIR = DATA_DIR / "predictions"
CACHE_DIR = DATA_DIR / "cache"
MODELS_DIR = PROJECT_ROOT / "models"
REPORTS_DIR = PROJECT_ROOT / "reports"

# Ensure runtime directories exist
for path in (
    RAW_DATA_DIR,
    PROCESSED_DATA_DIR,
    TRAINING_DATA_DIR,
    PREDICTIONS_DATA_DIR,
    CACHE_DIR,
    MODELS_DIR,
    REPORTS_DIR,
):
    path.mkdir(parents=True, exist_ok=True)

# Random Seed for Reproducibility
RANDOM_SEED = 42

# Column Definitions
METADATA_COLUMNS = [
    "Year",
    "RoundNumber",
    "EventName",
    "CircuitName",
    "SessionType",
    "SessionStartUtc",
    "Abbreviation",
    "DriverNumber",
    "TeamName",
    "DriverName",
    "team_lineage",
    "stage",
    "FeatureCutoffUtc",
    "history_source_max_utc",
    "weekend_source_max_utc",
    "weekend_source_session_types",
    "news_source_max_utc",
    "grid_source_max_utc",
]

TARGET_COLUMNS = [
    "target_position",
    "target_points",
    "target_winner",
    "target_podium",
    "target_pole",
    "target_dnf",
    "target_dnf_mechanical",
    "target_dnf_collision",
]

NON_FEATURE_COLUMNS = set(METADATA_COLUMNS + TARGET_COLUMNS + [
    "Position", "Points", "GridPosition",
    "prior_weekend_best_lap_seconds", "prior_weekend_mean_lap_seconds",
])


def get_feature_columns(frame):
    """Numeric model inputs only; all labels and provenance are excluded."""
    return [column for column in frame.select_dtypes(include=["number", "bool"]).columns
            if column not in NON_FEATURE_COLUMNS and not column.startswith("target_")]

# Weather columns
WEATHER_COLUMNS = [
    "prior_weekend_airtemp_mean",
    "prior_weekend_tracktemp_mean",
    "prior_weekend_humidity_mean",
    "prior_weekend_pressure_mean",
    "prior_weekend_windspeed_mean",
    "prior_weekend_rainfall_mean",
]

# Team Colors for UI / Charts
CONSTRUCTOR_COLORS = {
    "Red Bull Racing": "#3671C6",
    "Ferrari": "#E80020",
    "Mercedes": "#27F4D2",
    "McLaren": "#FF8000",
    "Aston Martin": "#229971",
    "Alpine": "#0093CC",
    "Williams": "#64C4FF",
    "RB": "#6692FF",
    "Kick Sauber": "#52E252",
    "Haas": "#B6BABD",
    "Alfa Romeo": "#C92D4B",
    "AlphaTauri": "#5E8FAA",
    "Racing Point": "#F596C8",
    "Renault": "#FFF500",
    "Toro Rosso": "#469BFF",
    "Racing Bulls": "#6692FF",
    "Audi": "#F50537",
    "Cadillac": "#AAAAAA",
}

DEFAULT_COLOR = "#E10600"  # F1 Red
