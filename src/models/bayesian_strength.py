"""Sequential Laplace filtering of driver, constructor and contextual PL strength.

The posterior is a Gaussian approximation, not an MCMC posterior. Proper
zero-sum entity contrasts and fixed shrinkage priors identify the latent effects.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.linalg import cho_factor, cho_solve, helmert
from scipy.optimize import minimize
from scipy.special import logsumexp
from sklearn.impute import SimpleImputer
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from src.data.outcomes import dnf_labels
from src.utils.logging import setup_logger

logger = setup_logger(__name__)


def pl_derivatives(scores: np.ndarray) -> tuple[float, np.ndarray, np.ndarray]:
    """Return PL negative log likelihood, gradient and Hessian in finish order."""
    n = len(scores)
    gradient = np.zeros(n)
    hessian = np.zeros((n, n))
    loss = 0.0
    for k in range(n - 1):
        normalizer = logsumexp(scores[k:])
        probabilities = np.exp(scores[k:] - normalizer)
        loss += normalizer - scores[k]
        gradient[k:] += probabilities
        gradient[k] -= 1
        hessian[k:, k:] += np.diag(probabilities) - np.outer(probabilities, probabilities)
    return float(loss), gradient, hessian


class DynamicBayesianStrength:
    """Filter conditional-finisher ranks with evolving, partially pooled effects."""

    driver_prior_variance = 0.5**2
    team_prior_variance = 1.0
    context_prior_variance = 0.5**2
    driver_process_variance = 0.035**2
    team_process_variance = 0.10**2

    @staticmethod
    def _entities(frame: pd.DataFrame, candidates: tuple[str, ...]) -> pd.Series:
        column = next((name for name in candidates if name in frame), None)
        if column is None or frame[column].isna().any():
            raise ValueError(f"Bayesian strength requires nonmissing identities: {candidates}")
        values = frame[column].astype(str)
        if values.str.strip().eq("").any():
            raise ValueError("Bayesian entity identities must not be empty")
        return values

    @staticmethod
    def _era(year: int) -> int:
        return 2026 if year >= 2026 else 2022 if year >= 2022 else 2014

    def _design(self, frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        drivers = self._entities(frame, (self.driver_column_,))
        teams = self._entities(frame, (self.team_column_,))
        design = np.zeros((len(frame), len(self.mean_)))
        unseen = np.zeros((len(frame), len(frame)))
        for entities, levels, contrasts, section, prior in (
            (drivers, self.drivers_, self.driver_contrasts_, self.driver_slice_, self.driver_prior_variance),
            (teams, self.teams_, self.team_contrasts_, self.team_slice_, self.team_prior_variance),
        ):
            indices = entities.map(levels)
            known = indices.notna().to_numpy()
            design[known, section] = contrasts[indices[known].to_numpy(int)]
            unknown = ~known
            same_entity = entities.to_numpy()[:, None] == entities.to_numpy()[None, :]
            unseen += prior * (unknown[:, None] & unknown[None, :] & same_entity)
        if self.context_columns_:
            values = frame.reindex(columns=self.context_columns_).replace([np.inf, -np.inf], np.nan)
            design[:, self.context_slice_] = self.context_transform_.transform(values)
        return design, unseen

    def _transition(self, start: pd.Timestamp, era: int) -> tuple[np.ndarray, np.ndarray]:
        elapsed = max(0.0, (start - self.last_start_).total_seconds() / (14 * 86400))
        mean = self.mean_.copy()
        covariance = self.covariance_.copy()
        diffusion = np.zeros(len(mean))
        diffusion[self.driver_slice_] = self.driver_process_variance * elapsed
        diffusion[self.team_slice_] = self.team_process_variance * elapsed
        covariance += np.diag(diffusion)
        if era != self.last_era_:
            retention = np.ones(len(mean))
            retention[self.team_slice_] = 0.25
            mean *= retention
            covariance *= np.outer(retention, retention)
            reset = np.zeros(len(mean))
            reset[self.team_slice_] = self.team_prior_variance * (1 - 0.25**2)
            covariance += np.diag(reset)
        return mean, covariance

    def fit(self, frame: pd.DataFrame, context_columns: list[str]) -> DynamicBayesianStrength:
        """Fit chronological Gaussian updates using only supplied training races."""
        from src.models.race_model import chronological_races

        if frame.empty or not {"SessionStartUtc", "target_position"}.issubset(frame):
            raise ValueError("Bayesian training requires dated races and finishing positions")
        races = chronological_races(frame)
        self.driver_column_ = next((c for c in ("Abbreviation", "DriverNumber") if c in frame), "Abbreviation")
        self.team_column_ = next((c for c in ("team_lineage", "TeamName") if c in frame), "TeamName")
        drivers = self._entities(frame, (self.driver_column_,))
        teams = self._entities(frame, (self.team_column_,))
        self.drivers_ = {name: i for i, name in enumerate(sorted(drivers.unique()))}
        self.teams_ = {name: i for i, name in enumerate(sorted(teams.unique()))}
        self.driver_contrasts_ = helmert(len(self.drivers_), full=False).T
        self.team_contrasts_ = helmert(len(self.teams_), full=False).T
        d, t = self.driver_contrasts_.shape[1], self.team_contrasts_.shape[1]
        self.context_columns_ = [c for c in context_columns if c in frame]
        self.driver_slice_, self.team_slice_ = slice(0, d), slice(d, d + t)
        self.context_slice_ = slice(d + t, d + t + len(self.context_columns_))
        if self.context_columns_:
            self.context_transform_ = make_pipeline(
                SimpleImputer(strategy="median", keep_empty_features=True), StandardScaler())
            self.context_transform_.fit(
                frame[self.context_columns_].replace([np.inf, -np.inf], np.nan))
        self.mean_ = np.zeros(d + t + len(self.context_columns_))
        if not len(self.mean_):
            raise ValueError("Bayesian strength needs at least two entities or a contextual feature")
        variances = np.r_[np.full(d, self.driver_prior_variance),
                          np.full(t, self.team_prior_variance),
                          np.full(len(self.context_columns_), self.context_prior_variance)]
        self.covariance_ = np.diag(variances)
        first = frame.iloc[races[0]]
        self.last_start_ = pd.to_datetime(first.SessionStartUtc.iloc[0], utc=True)
        self.last_era_ = self._era(int(first.Year.iloc[0]))
        self.n_updates_ = 0
        for rows in races:
            race = frame.iloc[rows].sort_values(self.driver_column_, kind="stable")
            position = pd.to_numeric(race.target_position, errors="coerce").to_numpy(float)
            if not np.array_equal(np.sort(position), np.arange(1, len(race) + 1)):
                raise ValueError("Bayesian training requires complete unique positions 1..N")
            if race[self.driver_column_].duplicated().any():
                raise ValueError("Duplicate Bayesian driver in race")
            start = pd.to_datetime(race.SessionStartUtc.iloc[0], utc=True)
            era = self._era(int(race.Year.iloc[0]))
            prior_mean, prior_covariance = self._transition(start, era)
            eligible = ~dnf_labels(race)[0].eq(1).to_numpy()
            design, _ = self._design(race)
            design = design[eligible][np.argsort(position[eligible])]
            self.mean_, self.covariance_ = prior_mean, prior_covariance
            if len(design) > 1:
                precision = cho_solve(cho_factor(prior_covariance), np.eye(len(prior_mean)))

                def objective(theta: np.ndarray, design=design, prior_mean=prior_mean,
                              precision=precision) -> tuple[float, np.ndarray]:
                    loss, gradient, _ = pl_derivatives(design @ theta)
                    delta = theta - prior_mean
                    return loss + 0.5 * delta @ precision @ delta, design.T @ gradient + precision @ delta

                def hessian(theta: np.ndarray, design=design, precision=precision) -> np.ndarray:
                    return precision + design.T @ pl_derivatives(design @ theta)[2] @ design

                result = minimize(objective, prior_mean, jac=True, hess=hessian, method="Newton-CG",
                                  options={"xtol": 1e-7, "maxiter": 100})
                if not result.success or not np.isfinite(result.x).all():
                    raise RuntimeError(f"Bayesian Laplace update failed at {start}: {result.message}")
                self.mean_ = result.x
                self.covariance_ = cho_solve(cho_factor(hessian(result.x)), np.eye(len(prior_mean)))
                self.covariance_ = (self.covariance_ + self.covariance_.T) / 2
                self.n_updates_ += 1
            self.last_start_, self.last_era_ = start, era
        if not self.n_updates_:
            logger.warning("No races with two eligible finishers; Bayesian strength retains its prior.")
        return self

    def posterior(self, frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        """Return centered strength mean and covariance for one future race."""
        if not hasattr(self, "mean_"):
            raise ValueError("Fit Bayesian strength before predicting")
        if frame.empty or not {"SessionStartUtc", "Year", "RoundNumber"}.issubset(frame):
            raise ValueError("Bayesian prediction requires one nonempty dated race")
        starts = pd.to_datetime(frame.SessionStartUtc, utc=True, errors="coerce")
        if starts.isna().any() or starts.nunique() != 1 or frame.Year.nunique() != 1:
            raise ValueError("Bayesian prediction requires one valid race start and year")
        if frame.RoundNumber.nunique() != 1 or frame[self.driver_column_].duplicated().any():
            raise ValueError("Bayesian prediction requires one race with unique drivers")
        start = starts.iloc[0]
        if start <= self.last_start_:
            raise ValueError("Bayesian prediction must be strictly after the last training race")
        design, unseen = self._design(frame)
        mean, covariance = self._transition(start, self._era(int(frame.Year.iloc[0])))
        center = np.eye(len(frame)) - np.ones((len(frame), len(frame))) / len(frame)
        strengths = center @ design @ mean
        covariance = center @ (design @ covariance @ design.T + unseen) @ center
        return strengths, (covariance + covariance.T) / 2

    def training_scale(self, frame: pd.DataFrame) -> float:
        """Compute a score scale from training inputs, never the prediction field."""
        design, _ = self._design(frame)
        scores = design @ self.mean_
        groups = frame.Year.astype(str) + "_" + frame.RoundNumber.astype(str)
        centered = scores - pd.Series(scores).groupby(groups.to_numpy()).transform("mean").to_numpy()
        return max(float(np.sqrt(np.mean(centered**2))), 1e-6)
