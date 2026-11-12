"""Probability calibration and race-level distribution reconciliation."""

import re
from typing import Optional

import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar
from scipy.special import logsumexp
from sklearn.base import BaseEstimator, ClassifierMixin, clone
from src.observability.operations import observe

CALIBRATION_OBJECTIVE = "final_win_podium_top10_plus_conditional_top10_pl"


def top10_pl_nll(scores, finish_order, temperature=1.0):
    """Top-10 ordered PL loss, retaining unselected entrants in each denominator."""
    scores = np.asarray(scores, dtype=float)
    order = np.asarray(finish_order, dtype=int)[:10]
    if temperature <= 0 or not np.isfinite(temperature) or not np.isfinite(scores).all():
        raise ValueError("PL scoring requires finite scores and a positive temperature")
    if len(np.unique(order)) != len(order) or (order < 0).any() or (order >= len(scores)).any():
        raise ValueError("PL order must contain unique valid entrant indices")
    remaining = np.ones(len(scores), dtype=bool)
    scaled = scores / temperature
    loss = 0.0
    for entrant in order:
        loss += logsumexp(scaled[remaining]) - scaled[entrant]
        remaining[entrant] = False
    return float(loss)


def simulation_log_loss(examples, temperature=1.0, n_simulations=1024, seed=42):
    """Final outcome log losses plus mean conditional-finisher top-10 PL loss."""
    from src.simulation.race_simulator import MonteCarloRaceSimulator, session_scenario
    from src.data.outcomes import dnf_labels

    if not examples:
        raise ValueError("Simulation scoring requires at least one held-out race")
    values = []
    for i, example in enumerate(examples):
        frame = example["frame"].reset_index(drop=True)
        grid = frame.copy()
        grid["Abbreviation"] = np.arange(len(frame))
        posterior = ({"strength_covariance": example["strength_covariance"]}
                     if example.get("strength_covariance") is not None else {})
        prediction = MonteCarloRaceSimulator(n_simulations, seed + i).simulate(
            grid, session_scenario(frame), strength=example["strength"],
            p_dnf=example["p_dnf"], pl_temperature=temperature,
            race_correlation=example.get("race_correlation", 0.0), **posterior)
        prediction = prediction.set_index("Abbreviation").reindex(np.arange(len(frame)))
        position = frame.target_position.to_numpy(float)
        winner = int(position.argmin())
        p = np.clip(prediction.podium_probability.to_numpy(), 1e-12, 1 - 1e-12)
        podium = position <= min(3, len(frame))
        p10 = np.clip(prediction.top10_probability.to_numpy(), 1e-12, 1 - 1e-12)
        top10 = position <= min(10, len(frame))
        finishers = np.flatnonzero(dnf_labels(frame)[0].eq(0).to_numpy())
        finisher_order = np.argsort(position[finishers])
        pl_loss = top10_pl_nll(np.asarray(example["strength"])[finishers], finisher_order, temperature)
        pl_loss /= max(1, min(10, len(finishers)))
        values.append(-np.log(np.clip(prediction.win_probability.iloc[winner], 1e-12, 1))
                      - np.mean(podium * np.log(p) + (~podium) * np.log1p(-p))
                      - np.mean(top10 * np.log(p10) + (~top10) * np.log1p(-p10)) + pl_loss)
    return float(np.mean(values))


@observe("ml.calibrate")
def fit_simulation_temperature(examples, default=1.0, n_simulations=1024, seed=42):
    """Fit the PL scale against final OOF win, podium and top-10 probabilities.

    Each example supplies past-only strength/risk predictions and a complete
    held-out race. Common random numbers make candidate comparisons repeatable.
    """
    if not examples:
        return float(default)
    cache = {}

    def loss(temperature):
        key = float(temperature)
        if key not in cache:
            cache[key] = simulation_log_loss(examples, key, n_simulations=n_simulations, seed=seed)
        return cache[key]

    candidates = np.unique(np.r_[np.geomspace(.3, 5, 7), default])
    best = int(np.argmin([loss(t) for t in candidates]))
    lower, upper = candidates[max(0, best - 1)], candidates[min(len(candidates) - 1, best + 1)]
    if lower < upper:
        refined = minimize_scalar(loss, bounds=(lower, upper), method="bounded",
                                  options={"xatol": .04, "maxiter": 10})
        loss(refined.x)
    return min(cache, key=cache.get)


def fit_temperature(scores_by_race, winner_idx_by_race, default=1.0):
    """Fit only caller-supplied held-out scores; one winner per race."""
    if not scores_by_race:
        return float(default)

    def loss(temperature):
        return np.mean([logsumexp(np.asarray(s) / temperature) - np.asarray(s)[w] / temperature
                        for s, w in zip(scores_by_race, winner_idx_by_race)])

    return float(minimize_scalar(loss, bounds=(0.3, 5.0), method="bounded").x)


def pl_nll(scores, finish_order, temperature=1.0):
    """Negative PL log likelihood of the supplied (possibly finisher-only) order."""
    ordered = np.asarray(scores, dtype=float)[np.asarray(finish_order, dtype=int)] / temperature
    if not len(ordered):
        return 0.0
    return float((np.logaddexp.accumulate(ordered[::-1])[::-1] - ordered).sum())


def fit_pl_temperature(scores_by_race, orders, default=1.0):
    usable = [(s, order) for s, order in zip(scores_by_race, orders) if len(order) > 1]
    if not usable:
        return float(default)
    return float(minimize_scalar(
        lambda temperature: np.mean([top10_pl_nll(s, order, temperature) for s, order in usable]),
        bounds=(0.3, 5.0), method="bounded").x)


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
        """Expanding race OOF calibration, then refit the estimator on all rows.

        Year_round group keys are ordered numerically; other keys must be supplied
        in chronological first-occurrence order.
        With fewer than three races no temperature is learned.
        """
        self.fitted_temperature_ = self.temperature
        self.calibration_provenance_ = {"method": "expanding_race_oof", "folds": [], "n_races": 0}
        scores, winners = [], []
        if groups is not None and self.method == "temperature":
            group_values = np.asarray(groups)
            labels = pd.unique(group_values)
            if all(re.fullmatch(r"\d+(?:\.0)?_\d+(?:\.0)?", str(label)) for label in labels):
                labels = np.asarray(sorted(labels, key=lambda label: tuple(float(part) for part in str(label).split("_"))))
            starts = np.array_split(np.arange(max(2, len(labels) // 3), len(labels)), 4)
            for block in starts:
                if not len(block):
                    continue
                train = np.flatnonzero(np.isin(group_values, labels[:block[0]]))
                valid = np.flatnonzero(np.isin(group_values, labels[block]))
                if len(np.unique(np.asarray(y)[train])) < 2:
                    continue
                estimator = clone(self.base_estimator)
                estimator.fit(X.iloc[train], np.asarray(y)[train])
                wrapper = CalibratedRacePredictor(estimator)
                raw = wrapper._get_raw_scores(X.iloc[valid])
                for label in labels[block]:
                    mask = group_values[valid] == label
                    truth = np.asarray(y)[valid][mask]
                    if truth.sum() == 1:
                        scores.append(raw[mask])
                        winners.append(int(truth.argmax()))
                self.calibration_provenance_["folds"].append({
                    "train_groups": labels[:block[0]].tolist(), "validation_groups": labels[block].tolist()})
            self.fitted_temperature_ = fit_temperature(scores, winners, self.temperature)
        self.calibration_provenance_["n_races"] = len(scores)
        self.base_estimator.fit(X, y)
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
