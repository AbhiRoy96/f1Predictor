"""Training and evaluation of finishing position prediction models."""

import argparse
from pathlib import Path
from typing import Dict, List, Optional
import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from xgboost import XGBRegressor

from src.models.evaluate import compute_position_metrics
from src.models.train_winner import extract_features_and_target
from src.utils.config import MODELS_DIR, PROCESSED_DATA_DIR, RANDOM_SEED
from src.utils.logging import setup_logger

logger = setup_logger(__name__)


def create_position_pipeline() -> Pipeline:
    """Create a tuned XGBoost regressor pipeline for finishing position."""
    return Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("scaler", StandardScaler()),
        ("regressor", XGBRegressor(
            n_estimators=100,
            max_depth=4,
            learning_rate=0.05,
            random_state=RANDOM_SEED,
            n_jobs=-1,
        )),
    ])


def train_and_save_position_model(
    race_features_path: Optional[Path] = None,
    output_dir: Path = MODELS_DIR,
) -> Dict[str, any]:
    """Train position prediction model and save model artifact."""
    path = race_features_path or (PROCESSED_DATA_DIR / "race_features.parquet")
    if not path.exists():
        raise FileNotFoundError(f"Feature dataset not found at {path}. Run feature engineering first.")

    df = pd.read_parquet(path)
    # Filter rows with valid target position
    valid_mask = df["target_position"].notna() & (df["target_position"] > 0)
    df_valid = df[valid_mask].copy()

    X, y, feature_cols = extract_features_and_target(df_valid, target_col="target_position")
    groups = df_valid["Year"].astype(str) + "_" + df_valid["RoundNumber"].astype(str)

    logger.info(f"Training position model on {len(df_valid)} rows with {len(feature_cols)} features...")
    pipeline = create_position_pipeline()
    pipeline.fit(X, y)

    # In-sample evaluation
    y_pred = pipeline.predict(X)
    metrics = compute_position_metrics(y, y_pred, groups=groups)
    logger.info(f"Position Model Metrics: {metrics}")

    # Serialize
    save_path = output_dir / "position_model.joblib"
    joblib.dump(
        {
            "model": pipeline,
            "feature_columns": feature_cols,
            "metrics": metrics,
        },
        save_path,
    )
    logger.info(f"Position model saved to {save_path}")

    return {
        "model_path": save_path,
        "feature_columns": feature_cols,
        "metrics": metrics,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Train F1 finishing position prediction model.")
    parser.add_argument("--features", type=str, default=None, help="Path to race_features.parquet")
    args = parser.parse_args()

    features_file = Path(args.features) if args.features else None
    train_and_save_position_model(features_file)
