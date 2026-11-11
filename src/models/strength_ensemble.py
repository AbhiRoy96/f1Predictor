"""Training-scaled Bayesian/XGBoost stacking with final-simulation OOF scoring."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar

from src.models.bayesian_strength import DynamicBayesianStrength
from src.models.calibrate import simulation_log_loss
from src.utils.logging import setup_logger
from src.observability.operations import observe

logger = setup_logger(__name__)


@dataclass
class BayesianXGBComponents:
    """Keep both fitted components and their training-only normalization scales."""

    bayesian: DynamicBayesianStrength
    xgb: tuple
    bayesian_scale: float
    xgb_scale: float


def training_score_scale(scores: np.ndarray, frame: pd.DataFrame) -> float:
    """Use training-race centered RMS, not the held-out field's score variance."""
    groups = frame.Year.astype(str) + "_" + frame.RoundNumber.astype(str)
    centered = scores - pd.Series(scores).groupby(groups.to_numpy()).transform("mean").to_numpy()
    scale = float(np.sqrt(np.mean(centered**2)))
    if not np.isfinite(scale):
        raise ValueError("Ensemble training scores must be finite")
    if scale < 1e-6:
        logger.warning("Constant training scores in ensemble component; using unit normalization.")
        return 1.0
    return scale


def blend_distribution(scores: np.ndarray, covariance: np.ndarray, weight: float) -> tuple[np.ndarray, np.ndarray]:
    """Blend normalized scores and propagate Bayesian covariance by weight squared."""
    if not np.isfinite(weight) or not 0 <= weight <= 1:
        raise ValueError("Bayesian ensemble weight must lie in [0, 1]")
    scores = np.asarray(scores, dtype=float)
    covariance = np.asarray(covariance, dtype=float)
    if (scores.ndim != 2 or scores.shape[1] != 2 or not np.isfinite(scores).all()
            or covariance.shape != (len(scores), len(scores)) or not np.isfinite(covariance).all()):
        raise ValueError("Ensemble requires finite two-component scores and matching covariance")
    return scores @ np.array([weight, 1 - weight]), covariance * weight**2


@observe("ml.ensemble.weight")
def fit_ensemble_weight(examples: list[dict], default: float = .5,
                        n_simulations: int = 512, seed: int = 42) -> float:
    """Select a weight on OOF final win/podium loss, before final temperature fitting."""
    if not np.isfinite(default) or not 0 <= default <= 1:
        raise ValueError("Default ensemble weight must lie in [0, 1]")
    if not examples:
        logger.warning("No OOF races for ensemble weights; retaining the fixed prior weight %.3f.", default)
        return float(default)
    losses = {}

    def loss(weight: float) -> float:
        key = float(weight)
        if key not in losses:
            blended = []
            for example in examples:
                strength, covariance = blend_distribution(
                    example["component_scores"], example["strength_covariance"], key)
                blended.append(dict(example, strength=strength, strength_covariance=covariance))
            losses[key] = simulation_log_loss(blended, 1.0, n_simulations=n_simulations, seed=seed)
        return losses[key]

    candidates = np.unique(np.r_[np.linspace(0, 1, 11), default])
    best = int(np.argmin([loss(weight) for weight in candidates]))
    lower, upper = candidates[max(0, best - 1)], candidates[min(len(candidates) - 1, best + 1)]
    if lower < upper:
        refined = minimize_scalar(loss, bounds=(lower, upper), method="bounded",
                                  options={"xatol": .025, "maxiter": 8})
        loss(refined.x)
    return min(losses, key=losses.get)
