"""Legacy standalone podium baseline; use RacePredictor for production probabilities."""

import argparse
from pathlib import Path
from typing import Dict, List, Optional
from src.models.artifacts import dump_artifact
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from xgboost import XGBClassifier

from src.models.evaluate import compute_podium_metrics
from src.models.train_winner import extract_features_and_target
from src.utils.config import MODELS_DIR, PROCESSED_DATA_DIR, RANDOM_SEED
from src.utils.logging import setup_logger

logger = setup_logger(__name__)


def create_podium_pipeline() -> Pipeline:
    """Create a tuned XGBoost pipeline for podium classification."""
    return Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("scaler", StandardScaler()),
        ("classifier", XGBClassifier(
            n_estimators=100,
            max_depth=4,
            learning_rate=0.05,
            eval_metric="logloss",
            random_state=RANDOM_SEED,
            n_jobs=-1,
        )),
    ])


def train_and_save_podium_model(
    race_features_path: Optional[Path] = None,
    output_dir: Path = MODELS_DIR,
) -> Dict[str, any]:
    """Train podium prediction model and save model artifact."""
    path = race_features_path or (PROCESSED_DATA_DIR / "race_features.parquet")
    if not path.exists():
        raise FileNotFoundError(f"Feature dataset not found at {path}. Run feature engineering first.")

    df = pd.read_parquet(path)
    X, y, feature_cols = extract_features_and_target(df, target_col="target_podium")

    logger.info(f"Training podium model on {len(df)} rows with {len(feature_cols)} features...")
    pipeline = create_podium_pipeline()
    pipeline.fit(X, y)

    # In-sample evaluation
    y_prob = pipeline.predict_proba(X)[:, 1]
    metrics = compute_podium_metrics(y, y_prob)
    logger.info(f"Podium Model Metrics: {metrics}")

    # Serialize
    save_path = output_dir / "podium_model.joblib"
    dump_artifact(
        {
            "model": pipeline,
            "feature_columns": feature_cols,
            "metrics": metrics,
        },
        save_path,
    )
    logger.info(f"Podium model saved to {save_path}")

    return {
        "model_path": save_path,
        "feature_columns": feature_cols,
        "metrics": metrics,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Train F1 podium prediction model.")
    parser.add_argument("--features", type=str, default=None, help="Path to race_features.parquet")
    args = parser.parse_args()

    features_file = Path(args.features) if args.features else None
    train_and_save_podium_model(features_file)
