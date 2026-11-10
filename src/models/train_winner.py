"""Training, calibration, and evaluation of race winner prediction models."""

import argparse
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from xgboost import XGBClassifier

from src.models.calibrate import CalibratedRacePredictor
from src.models.evaluate import compute_winner_metrics, run_chronological_backtest
from src.utils.config import MODELS_DIR, PROCESSED_DATA_DIR, RANDOM_SEED, get_feature_columns
from src.utils.logging import setup_logger

logger = setup_logger(__name__)
DEFAULT_ENSEMBLE_WEIGHTS = [0.2, 0.3, 0.5]
BACKTEST_MODEL_NAMES = ("LogisticRegression", "RandomForest", "XGBoost")


def derive_ensemble_weights(backtest_results: Dict[str, Dict[str, Any]]) -> List[float]:
    """Convert chronological grouped log losses to normalized inverse-loss weights."""
    losses = []
    for model_name in BACKTEST_MODEL_NAMES:
        try:
            loss = float(backtest_results[model_name]["mean_grouped_log_loss"])
        except (KeyError, TypeError, ValueError):
            return DEFAULT_ENSEMBLE_WEIGHTS.copy()
        if not np.isfinite(loss) or loss <= 0:
            return DEFAULT_ENSEMBLE_WEIGHTS.copy()
        losses.append(loss)

    inverse_losses = 1.0 / np.asarray(losses, dtype=float)
    weights = inverse_losses / inverse_losses.sum()
    return weights.tolist()


def extract_features_and_target(
    df: pd.DataFrame,
    target_col: str = "target_winner",
) -> Tuple[pd.DataFrame, pd.Series, List[str]]:
    """Extract numeric feature matrix X, target series y, and feature column names."""
    feature_cols = get_feature_columns(df)
    if not feature_cols:
        raise ValueError("No numeric feature columns found in dataset")

    X = df[feature_cols].copy()
    y = df[target_col].fillna(0).astype(int) if target_col in df.columns else pd.Series(0, index=df.index)
    return X, y, feature_cols


def create_logistic_regression_pipeline() -> Pipeline:
    """Logistic regression probability model baseline."""
    return Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("scaler", StandardScaler()),
        ("classifier", LogisticRegression(max_iter=1000, random_state=RANDOM_SEED, C=0.1)),
    ])


def create_random_forest_pipeline() -> Pipeline:
    """Random forest nonlinear tabular baseline."""
    return Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("classifier", RandomForestClassifier(n_estimators=100, max_depth=6, random_state=RANDOM_SEED, n_jobs=-1)),
    ])


def create_xgboost_pipeline() -> Pipeline:
    """XGBoost gradient boosting classifier."""
    return Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("classifier", XGBClassifier(
            n_estimators=100,
            max_depth=4,
            learning_rate=0.05,
            eval_metric="logloss",
            random_state=RANDOM_SEED,
            n_jobs=-1,
        )),
    ])


class EnsembleRaceWinnerModel:
    """Calibrated weighted ensemble combining Logistic Regression, Random Forest, and XGBoost."""

    def __init__(self, weights: Optional[List[float]] = None):
        self.weights = list(weights) if weights is not None else DEFAULT_ENSEMBLE_WEIGHTS.copy()
        self.models = {
            "lr": CalibratedRacePredictor(create_logistic_regression_pipeline()),
            "rf": CalibratedRacePredictor(create_random_forest_pipeline()),
            "xgb": CalibratedRacePredictor(create_xgboost_pipeline()),
        }
        self.feature_names_in_ = []

    def fit(self, X: pd.DataFrame, y: pd.Series, groups: Optional[pd.Series] = None):
        self.feature_names_in_ = list(X.columns)
        for name, model in self.models.items():
            model.fit(X, y, groups=groups)
        return self

    def predict_proba(self, X: pd.DataFrame, groups: Optional[pd.Series] = None) -> np.ndarray:
        p_ensemble = np.zeros(len(X))
        total_w = sum(self.weights)

        for w, (name, model) in zip(self.weights, self.models.items()):
            probs = model.predict_proba(X, groups=groups)[:, 1]
            p_ensemble += (w / total_w) * probs

        # Renormalize by race group to ensure exact sum to 1.0
        if groups is None:
            if "Year" in X.columns and "RoundNumber" in X.columns:
                groups = X["Year"].astype(str) + "_" + X["RoundNumber"].astype(str)
            else:
                groups = pd.Series("race_0", index=X.index)

        df = pd.DataFrame({"p": p_ensemble, "group": groups.values})
        for _, grp in df.groupby("group"):
            total = grp["p"].sum()
            if total > 0:
                p_ensemble[grp.index] = grp["p"] / total
            else:
                p_ensemble[grp.index] = 1.0 / len(grp)

        p0 = 1.0 - p_ensemble
        return np.column_stack([p0, p_ensemble])

    def predict(self, X: pd.DataFrame, groups: Optional[pd.Series] = None) -> np.ndarray:
        proba = self.predict_proba(X, groups=groups)[:, 1]
        if groups is None:
            if "Year" in X.columns and "RoundNumber" in X.columns:
                groups = X["Year"].astype(str) + "_" + X["RoundNumber"].astype(str)
            else:
                groups = pd.Series("race_0", index=X.index)

        preds = np.zeros(len(X), dtype=int)
        df = pd.DataFrame({"proba": proba, "group": groups.values})
        for _, grp in df.groupby("group"):
            max_idx = grp["proba"].idxmax()
            preds[max_idx] = 1
        return preds


def train_and_save_winner_models(
    race_features_path: Optional[Path] = None,
    output_dir: Path = MODELS_DIR,
) -> Dict[str, Any]:
    """Train winner prediction models, perform backtesting, and serialize the best ensemble."""
    path = race_features_path or (PROCESSED_DATA_DIR / "race_features.parquet")

    if not path.exists():
        raise FileNotFoundError(f"Feature dataset not found at {path}. Run feature engineering first.")

    df = pd.read_parquet(path)
    X, y, feature_cols = extract_features_and_target(df, target_col="target_winner")
    groups = df["Year"].astype(str) + "_" + df["RoundNumber"].astype(str)

    logger.info(f"Loaded {len(df)} rows across {df['Year'].nunique()} seasons with {len(feature_cols)} features.")

    # 1. Backtest individual models
    models_to_evaluate = {
        "LogisticRegression": lambda: CalibratedRacePredictor(create_logistic_regression_pipeline()),
        "RandomForest": lambda: CalibratedRacePredictor(create_random_forest_pipeline()),
        "XGBoost": lambda: CalibratedRacePredictor(create_xgboost_pipeline()),
    }

    backtest_results = {}
    for name, factory in models_to_evaluate.items():
        logger.info(f"Running chronological backtest for {name}...")
        res = run_chronological_backtest(df, factory, feature_cols, target_col="target_winner")
        backtest_results[name] = res["summary"]
        logger.info(f"{name} Results: {res['summary']}")

    # 2. Fit full ensemble on all training data
    logger.info("Training full Calibrated Ensemble on all historical data...")
    ensemble_weights = derive_ensemble_weights(backtest_results)
    ensemble = EnsembleRaceWinnerModel(weights=ensemble_weights)
    ensemble.fit(X, y, groups=groups)

    # Compute overall training metrics
    full_probs = ensemble.predict_proba(X, groups=groups)[:, 1]
    train_metrics = compute_winner_metrics(y, full_probs, groups)
    logger.info(f"Full Train Metrics: {train_metrics}")

    # 3. Save serialized model & artifacts
    model_save_path = output_dir / "winner_ensemble.joblib"
    joblib.dump(
        {
            "model": ensemble,
            "feature_columns": feature_cols,
            "train_metrics": train_metrics,
            "backtest_results": backtest_results,
            "ensemble_weights": ensemble_weights,
        },
        model_save_path,
    )
    logger.info(f"Model saved successfully to {model_save_path}")

    return {
        "model_path": model_save_path,
        "feature_columns": feature_cols,
        "train_metrics": train_metrics,
        "backtest_results": backtest_results,
        "ensemble_weights": ensemble_weights,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Train F1 race winner prediction models.")
    parser.add_argument("--features", type=str, default=None, help="Path to race_features.parquet")
    args = parser.parse_args()

    features_file = Path(args.features) if args.features else None
    train_and_save_winner_models(features_file)
