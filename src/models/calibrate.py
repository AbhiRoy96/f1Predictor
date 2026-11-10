"""Probability calibration and race-level distribution reconciliation."""

from typing import Optional, Union
import numpy as np
import pandas as pd
from scipy.optimize import minimize
from sklearn.base import BaseEstimator, ClassifierMixin


def normalize_race_probabilities(
    probs: np.ndarray,
    groups: pd.Series,
    min_prob: float = 1e-4,
) -> np.ndarray:
    """Normalize raw probabilities within each race group so they sum strictly to 1.0."""
    norm_probs = np.copy(probs)
    df = pd.DataFrame({"prob": norm_probs, "group": groups.values})

    for _, group_df in df.groupby("group"):
        p = np.clip(group_df["prob"].values, min_prob, None)
        total = np.sum(p)
        if total > 0:
            norm_probs[group_df.index] = p / total
        else:
            norm_probs[group_df.index] = 1.0 / len(group_df)

    return norm_probs


class CalibratedRacePredictor(BaseEstimator, ClassifierMixin):
    """Calibrates model outputs to produce valid race-level probability distributions."""

    def __init__(
        self,
        base_estimator: BaseEstimator,
        method: str = "temperature",
        temperature: float = 1.0,
    ):
        self.base_estimator = base_estimator
        self.method = method
        self.temperature = temperature
        self.fitted_temperature_ = temperature

    def fit(self, X: pd.DataFrame, y: pd.Series, groups: Optional[pd.Series] = None):
        """Fit the base estimator and optionally learn optimal temperature on validation set."""
        self.base_estimator.fit(X, y)

        if groups is not None and self.method == "temperature":
            # Learn optimal temperature scaling using log-loss on training/val predictions
            raw_scores = self._get_raw_scores(X)

            def loss_func(temp):
                t = max(temp[0], 0.05)
                # Compute softmax per group
                df = pd.DataFrame({"score": raw_scores / t, "group": groups.values, "y": y.values})
                total_loss = 0.0
                count = 0
                for _, grp in df.groupby("group"):
                    sc = grp["score"].values - np.max(grp["score"].values)
                    exp_sc = np.exp(sc)
                    p = exp_sc / np.sum(exp_sc)
                    p = np.clip(p, 1e-7, 1.0 - 1e-7)
                    y_true = grp["y"].values
                    total_loss -= np.sum(y_true * np.log(p))
                    count += 1
                return total_loss / max(count, 1)

            res = minimize(loss_func, [1.0], bounds=[(0.05, 10.0)], method="L-BFGS-B")
            self.fitted_temperature_ = float(res.x[0])
        else:
            self.fitted_temperature_ = self.temperature

        return self

    def _get_raw_scores(self, X: pd.DataFrame) -> np.ndarray:
        if hasattr(self.base_estimator, "decision_function"):
            return self.base_estimator.decision_function(X)
        elif hasattr(self.base_estimator, "predict_proba"):
            probs = self.base_estimator.predict_proba(X)
            p1 = probs[:, 1] if probs.shape[1] > 1 else probs[:, 0]
            # Convert to logit
            p1 = np.clip(p1, 1e-6, 1.0 - 1e-6)
            return np.log(p1 / (1.0 - p1))
        elif hasattr(self.base_estimator, "predict"):
            return self.base_estimator.predict(X)
        raise AttributeError("Base estimator lacks decision_function, predict_proba, or predict")

    def predict_proba(self, X: pd.DataFrame, groups: Optional[pd.Series] = None) -> np.ndarray:
        """Predict calibrated race-level win probabilities."""
        raw_scores = self._get_raw_scores(X)

        if groups is None:
            if "Year" in X.columns and "RoundNumber" in X.columns:
                groups = X["Year"].astype(str) + "_" + X["RoundNumber"].astype(str)
            else:
                groups = pd.Series("race_0", index=X.index)

        temp = max(self.fitted_temperature_, 0.05)
        df = pd.DataFrame({"score": raw_scores / temp, "group": groups.values})
        p1 = np.zeros(len(X))

        for _, grp in df.groupby("group"):
            sc = grp["score"].values - np.max(grp["score"].values)
            exp_sc = np.exp(sc)
            sum_exp = np.sum(exp_sc)
            if sum_exp > 0:
                p1[grp.index] = exp_sc / sum_exp
            else:
                p1[grp.index] = 1.0 / len(grp)

        p0 = 1.0 - p1
        return np.column_stack([p0, p1])

    def predict(self, X: pd.DataFrame, groups: Optional[pd.Series] = None) -> np.ndarray:
        """Predict winner (1 for highest predicted probability per race group, 0 otherwise)."""
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
