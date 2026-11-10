"""Benchmark baseline models for F1 race predictions."""

from typing import List, Optional
import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, ClassifierMixin


def _softmax_by_group(scores: pd.Series, group_keys: pd.Series, temperature: float = 1.0) -> np.ndarray:
    """Compute softmax probabilities per race group."""
    df = pd.DataFrame({"score": scores.values, "group": group_keys.values})
    probs = np.zeros(len(scores))
    for _, group in df.groupby("group"):
        sc = group["score"].values / max(temperature, 1e-5)
        # Avoid overflow
        sc = sc - np.max(sc)
        exp_sc = np.exp(sc)
        sum_exp = np.sum(exp_sc)
        if sum_exp > 0:
            probs[group.index] = exp_sc / sum_exp
        else:
            probs[group.index] = 1.0 / len(group)
    return probs


class PolePositionBaseline(BaseEstimator, ClassifierMixin):
    """Predicts winner strictly based on qualifying / grid position."""

    def __init__(self, position_col: str = "grid_position"):
        self.position_col = position_col

    def fit(self, X: pd.DataFrame, y: Optional[pd.Series] = None):
        return self

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        # Lower position value is better (P1 = best)
        col = self.position_col if self.position_col in X.columns else None
        if col is None:
            # Fallback to any available position indicator
            for candidate in ["GridPosition", "driver_qualifying_season_avg_position", "driver_race_season_avg_position"]:
                if candidate in X.columns:
                    col = candidate
                    break

        if col is not None:
            scores = -1.0 * X[col].fillna(20.0).astype(float)
        else:
            scores = pd.Series(0.0, index=X.index)

        group_keys = (
            X["Year"].astype(str) + "_" + X["RoundNumber"].astype(str)
            if "Year" in X.columns and "RoundNumber" in X.columns
            else pd.Series("race_0", index=X.index)
        )
        p1 = _softmax_by_group(scores, group_keys, temperature=0.5)
        p0 = 1.0 - p1
        return np.column_stack([p0, p1])

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        proba = self.predict_proba(X)[:, 1]
        group_keys = (
            X["Year"].astype(str) + "_" + X["RoundNumber"].astype(str)
            if "Year" in X.columns and "RoundNumber" in X.columns
            else pd.Series("race_0", index=X.index)
        )
        preds = np.zeros(len(X), dtype=int)
        df = pd.DataFrame({"proba": proba, "group": group_keys.values})
        for _, group in df.groupby("group"):
            max_idx = group["proba"].idxmax()
            preds[max_idx] = 1
        return preds


class ChampionshipLeaderBaseline(BaseEstimator, ClassifierMixin):
    """Predicts winner based on accumulated championship points."""

    def __init__(self, points_col: str = "driver_race_season_points"):
        self.points_col = points_col

    def fit(self, X: pd.DataFrame, y: Optional[pd.Series] = None):
        return self

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        col = self.points_col if self.points_col in X.columns else "driver_race_career_points"
        scores = X[col].fillna(0.0).astype(float) if col in X.columns else pd.Series(0.0, index=X.index)

        group_keys = (
            X["Year"].astype(str) + "_" + X["RoundNumber"].astype(str)
            if "Year" in X.columns and "RoundNumber" in X.columns
            else pd.Series("race_0", index=X.index)
        )
        p1 = _softmax_by_group(scores, group_keys, temperature=15.0)
        p0 = 1.0 - p1
        return np.column_stack([p0, p1])

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        proba = self.predict_proba(X)[:, 1]
        preds = np.zeros(len(X), dtype=int)
        group_keys = (
            X["Year"].astype(str) + "_" + X["RoundNumber"].astype(str)
            if "Year" in X.columns and "RoundNumber" in X.columns
            else pd.Series("race_0", index=X.index)
        )
        df = pd.DataFrame({"proba": proba, "group": group_keys.values})
        for _, group in df.groupby("group"):
            max_idx = group["proba"].idxmax()
            preds[max_idx] = 1
        return preds


class RecentFormBaseline(BaseEstimator, ClassifierMixin):
    """Predicts winner based on recent 5-race performance points."""

    def __init__(self, form_col: str = "driver_race_points_5"):
        self.form_col = form_col

    def fit(self, X: pd.DataFrame, y: Optional[pd.Series] = None):
        return self

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        col = self.form_col if self.form_col in X.columns else "driver_race_season_points"
        scores = X[col].fillna(0.0).astype(float) if col in X.columns else pd.Series(0.0, index=X.index)

        group_keys = (
            X["Year"].astype(str) + "_" + X["RoundNumber"].astype(str)
            if "Year" in X.columns and "RoundNumber" in X.columns
            else pd.Series("race_0", index=X.index)
        )
        p1 = _softmax_by_group(scores, group_keys, temperature=10.0)
        p0 = 1.0 - p1
        return np.column_stack([p0, p1])

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        proba = self.predict_proba(X)[:, 1]
        preds = np.zeros(len(X), dtype=int)
        group_keys = (
            X["Year"].astype(str) + "_" + X["RoundNumber"].astype(str)
            if "Year" in X.columns and "RoundNumber" in X.columns
            else pd.Series("race_0", index=X.index)
        )
        df = pd.DataFrame({"proba": proba, "group": group_keys.values})
        for _, group in df.groupby("group"):
            max_idx = group["proba"].idxmax()
            preds[max_idx] = 1
        return preds


class ConstructorStrengthBaseline(BaseEstimator, ClassifierMixin):
    """Predicts winner based on constructor season points."""

    def __init__(self, constructor_col: str = "constructor_race_season_points"):
        self.constructor_col = constructor_col

    def fit(self, X: pd.DataFrame, y: Optional[pd.Series] = None):
        return self

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        col = self.constructor_col if self.constructor_col in X.columns else "constructor_race_career_points"
        scores = X[col].fillna(0.0).astype(float) if col in X.columns else pd.Series(0.0, index=X.index)

        group_keys = (
            X["Year"].astype(str) + "_" + X["RoundNumber"].astype(str)
            if "Year" in X.columns and "RoundNumber" in X.columns
            else pd.Series("race_0", index=X.index)
        )
        p1 = _softmax_by_group(scores, group_keys, temperature=25.0)
        p0 = 1.0 - p1
        return np.column_stack([p0, p1])

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        proba = self.predict_proba(X)[:, 1]
        preds = np.zeros(len(X), dtype=int)
        group_keys = (
            X["Year"].astype(str) + "_" + X["RoundNumber"].astype(str)
            if "Year" in X.columns and "RoundNumber" in X.columns
            else pd.Series("race_0", index=X.index)
        )
        df = pd.DataFrame({"proba": proba, "group": group_keys.values})
        for _, group in df.groupby("group"):
            max_idx = group["proba"].idxmax()
            preds[max_idx] = 1
        return preds
