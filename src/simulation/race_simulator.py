"""Vectorized Plackett-Luce finishing orders, including retired drivers."""

import json

import numpy as np
import pandas as pd
from scipy.special import ndtr

from src.simulation.scenarios import RaceScenario, get_scenario
from src.utils.config import RANDOM_SEED


def simulate_race(strength, p_dnf, n_sims=20000, pl_temperature=1.0,
                  team_idx=None, team_sigma=0.0, seed=42, race_correlation=0.0,
                  strength_covariance=None):
    """Return (places, retired), both shaped (simulations, drivers)."""
    strength = np.asarray(strength, dtype=float)
    p_dnf = np.asarray(p_dnf, dtype=float)
    if strength.ndim != 1 or p_dnf.shape != strength.shape:
        raise ValueError("strength and p_dnf must be equally sized vectors")
    if not np.isfinite(strength).all() or not np.isfinite(p_dnf).all():
        raise ValueError("Simulation inputs must be finite")
    if np.any((p_dnf < 0) | (p_dnf > 1)):
        raise ValueError("DNF probabilities must lie in [0, 1]")
    if not isinstance(n_sims, (int, np.integer)) or n_sims <= 0:
        raise ValueError("n_sims must be a positive integer")
    if not np.isfinite(pl_temperature) or pl_temperature <= 0 or not np.isfinite(team_sigma) or team_sigma < 0:
        raise ValueError("Temperature must be positive and team_sigma nonnegative")
    if not np.isfinite(race_correlation) or not 0 <= race_correlation < 1:
        raise ValueError("race_correlation must lie in [0, 1)")
    rng = np.random.default_rng(seed)
    n = len(strength)
    sampled_strength = strength
    if strength_covariance is not None:
        covariance = np.asarray(strength_covariance, dtype=float)
        if (covariance.shape != (n, n) or not np.isfinite(covariance).all()
                or not np.allclose(covariance, covariance.T, rtol=0, atol=1e-10)):
            raise ValueError("Strength covariance must be finite, symmetric and shaped (drivers, drivers)")
        eigenvalues, eigenvectors = np.linalg.eigh(covariance)
        if np.any(eigenvalues < -1e-10):
            raise ValueError("Strength covariance must be positive semidefinite")
        factor = eigenvectors * np.sqrt(np.maximum(eigenvalues, 0))
        sampled_strength = strength + rng.normal(size=(n_sims, n)) @ factor.T
    util = sampled_strength / pl_temperature + rng.gumbel(size=(n_sims, n))
    if team_idx is not None and team_sigma > 0 and n:
        team_idx = np.asarray(team_idx)
        if team_idx.shape != strength.shape or np.any(team_idx < 0):
            raise ValueError("team_idx must contain one nonnegative team index per driver")
        _, team_idx = np.unique(team_idx, return_inverse=True)
        util += rng.normal(0, team_sigma, size=(n_sims, team_idx.max() + 1))[:, team_idx]
    if race_correlation > 0:
        common = rng.normal(size=(n_sims, 1))
        individual = rng.normal(size=(n_sims, n))
        failure_draw = ndtr(np.sqrt(race_correlation) * common + np.sqrt(1 - race_correlation) * individual)
        failure_draw = np.minimum(failure_draw, 1 - np.finfo(float).eps)
    else:
        failure_draw = rng.random((n_sims, n))
    retired = failure_draw < p_dnf
    hazard = -np.log1p(-np.minimum(p_dnf, 1 - 1e-12))
    distance = np.divide(-np.log1p(-failure_draw), hazard,
                         out=np.full_like(failure_draw, np.inf), where=hazard > 0)
    # Constant-hazard distance proxy: later retirements precede earlier ones.
    # Finishers remain ahead; this is not a lap-by-lap FIA classification model.
    order = np.lexsort((np.where(retired, -distance, -util), retired), axis=1)
    pos = np.empty_like(order)
    np.put_along_axis(pos, order, np.broadcast_to(np.arange(1, n + 1), order.shape), axis=1)
    return pos, retired


def summarize_positions(positions, retired, prior_mass=0.5):
    """Regularize orders conditional on sampled finisher/retiree groups.

    Half a simulated race of uniform within-group ordering reduces rare-order
    zeros without assigning a guaranteed retiree a win over guaranteed finishers.
    Mixing permutation distributions preserves every row/column sum exactly.
    MC standard errors describe numerical sampling, not model uncertainty.
    """
    n = positions.shape[1]
    count = len(positions)
    matrix = np.column_stack([(positions == place).sum(axis=0) for place in range(1, n + 1)])
    prior = np.zeros((n, n))
    finisher_count = (~retired).sum(axis=1)
    for finishers in np.unique(finisher_count):
        mask = finisher_count == finishers
        if finishers:
            prior[:, :finishers] += (~retired[mask]).sum(axis=0)[:, None] / (count * finishers)
        if finishers < n:
            prior[:, finishers:] += retired[mask].sum(axis=0)[:, None] / (count * (n - finishers))
    matrix = (matrix + prior_mass * prior) / (count + prior_mass)
    cumulative = matrix.cumsum(axis=1)
    p_win = matrix[:, 0]
    result = pd.DataFrame({
        "win_probability": p_win,
        "podium_probability": matrix[:, :3].sum(axis=1) if n > 3 else np.ones(n),
        "top10_probability": matrix[:, :10].sum(axis=1) if n > 10 else np.ones(n),
        "dnf_probability": retired.mean(axis=0),
        "expected_position": matrix @ np.arange(1, n + 1),
        "median_position": (cumulative < .5).sum(axis=1) + 1,
        "p10_position": (cumulative < .1).sum(axis=1) + 1,
        "p90_position": (cumulative < .9).sum(axis=1) + 1,
        "win_probability_mcse": np.sqrt(p_win * (1 - p_win) / count),
        "probability_smoothing_mass": prior_mass,
    })
    for position in range(1, n + 1):
        result[f"prob_p{position}"] = matrix[:, position - 1]
    return result


def session_scenario(drivers, scenario="standard_dry"):
    """Qualifying orders have no race-distance retirement or safety-car process."""
    if "SessionType" in drivers and drivers.SessionType.isin(["Q", "SQ"]).all():
        if scenario != "standard_dry":
            raise ValueError("Race scenarios are not supported for qualifying predictions")
        return RaceScenario("Qualifying order", "Conditional qualifying order, not a race simulation.",
                            safety_car_probability=0, vsc_probability=0,
                            weather_rain_probability=0, dnf_base_rate=0)
    return scenario


def simulate_saved_prediction(predictions, scenario="standard_dry", n_simulations=None, seed=None):
    """Reuse latent inputs, never reinterpret final win probabilities as strengths."""
    required = {"strength", "base_dnf_probability", "pl_temperature", "Abbreviation", "race_retirement_correlation"}
    missing = required.difference(predictions.columns)
    if missing or predictions.empty:
        raise ValueError(f"Regenerate predictions with saved simulation inputs; missing {sorted(missing)}")
    if predictions.Abbreviation.duplicated().any():
        raise ValueError("Saved prediction must contain one row per driver")
    if {"Year", "RoundNumber"}.issubset(predictions) and len(predictions[["Year", "RoundNumber"]].drop_duplicates()) != 1:
        raise ValueError("Scenario replay requires exactly one event")
    frame = predictions.sort_values("Abbreviation", kind="stable").reset_index(drop=True)
    for column in ("pl_temperature", "race_retirement_correlation", "simulation_count", "seed"):
        if column in frame and frame[column].nunique(dropna=False) != 1:
            raise ValueError(f"Saved prediction contains inconsistent {column}")
    if n_simulations is None:
        if "simulation_count" not in frame:
            raise ValueError("Saved prediction has no simulation_count")
        n_simulations = int(frame.simulation_count.iloc[0])
    if seed is None:
        if "seed" not in frame:
            raise ValueError("Saved prediction has no seed")
        seed = int(frame.seed.iloc[0])
    posterior = {}
    covariance_columns = {"strength_covariance_row", "strength_covariance_order"}
    if covariance_columns.intersection(frame.columns):
        if not covariance_columns.issubset(frame.columns):
            raise ValueError("Saved posterior requires covariance rows and driver order")
        if frame.strength_covariance_order.nunique(dropna=False) != 1:
            raise ValueError("Saved posterior contains inconsistent driver order")
        order = json.loads(frame.strength_covariance_order.iloc[0])
        identifiers = frame.Abbreviation.astype(str).tolist()
        if len(order) != len(frame) or len(set(order)) != len(order) or set(order) != set(identifiers):
            raise ValueError("Saved posterior driver order does not match the field")
        covariance = np.asarray([json.loads(row) for row in frame.strength_covariance_row], dtype=float)
        if covariance.shape != (len(frame), len(frame)):
            raise ValueError("Saved posterior covariance has invalid dimensions")
        posterior["strength_covariance"] = covariance[:, [order.index(name) for name in identifiers]]
    return MonteCarloRaceSimulator(n_simulations, seed).simulate(
        frame, session_scenario(frame, scenario), strength=frame.strength.to_numpy(float),
        p_dnf=frame.base_dnf_probability.to_numpy(float),
        pl_temperature=float(frame.pl_temperature.iloc[0]),
        race_correlation=float(frame.race_retirement_correlation.iloc[0]), **posterior)


class MonteCarloRaceSimulator:
    """Scenario wrapper; legacy win-probability inputs are interpreted as PL weights."""

    def __init__(self, n_simulations=2000, random_seed=RANDOM_SEED):
        if not isinstance(n_simulations, (int, np.integer)) or n_simulations < 1:
            raise ValueError("n_simulations must be a positive integer")
        self.n_simulations = n_simulations
        self.random_seed = random_seed

    def simulate(self, drivers_df, scenario="standard_dry", win_probabilities=None,
                 strength=None, p_dnf=None, pl_temperature=1.0, race_correlation=0.0,
                 strength_covariance=None):
        sc = get_scenario(scenario) if isinstance(scenario, str) else scenario
        if not isinstance(sc, RaceScenario):
            raise TypeError("scenario must be a name or RaceScenario")
        if drivers_df.empty:
            return pd.DataFrame()
        drivers = drivers_df.reset_index(drop=True)
        n = len(drivers)
        if strength is None:
            if win_probabilities is not None:
                probabilities = np.asarray(win_probabilities, dtype=float)
                if probabilities.shape != (n,) or not np.isfinite(probabilities).all() or np.any(probabilities < 0):
                    raise ValueError("Invalid win probabilities")
                strength = np.log(np.clip(probabilities, 1e-12, None))
            else:
                col = next((c for c in ["grid_position", "GridPosition", "driver_qualifying_season_avg_position"]
                            if c in drivers), None)
                strength = -drivers[col].fillna((n + 1) / 2).to_numpy(float) / 3 if col else np.zeros(n)
        strength = np.asarray(strength, dtype=float).copy()
        if strength.shape != (n,):
            raise ValueError("One strength is required per driver")
        rng = np.random.default_rng(self.random_seed)
        # SC/VSC/rain change each simulated race, not just its overall scale.
        rain = rng.random(self.n_simulations) < sc.weather_rain_probability
        safety = rng.random(self.n_simulations) < sc.safety_car_probability
        vsc = rng.random(self.n_simulations) < sc.vsc_probability
        temperature = pl_temperature * sc.pl_temperature * sc.pace_variance_scale
        temperature *= np.sqrt(sc.tyre_deg_multiplier)
        grid_col = next((c for c in ["grid_position", "GridPosition"] if c in drivers), None)
        if grid_col:
            grid = drivers[grid_col].where(drivers[grid_col] > 0).fillna((n + 1) / 2).to_numpy(float)
            strength -= (sc.overtaking_difficulty - 0.5) * (grid - grid.mean()) / 3
        team_idx = pd.factorize(drivers.get("TeamName", pd.Series(np.arange(n))).fillna("Unknown"))[0]
        if p_dnf is None:
            risks = np.full(n, sc.dnf_base_rate)
        else:
            risks = np.asarray(p_dnf, dtype=float)
            if risks.shape != (n,) or not np.isfinite(risks).all() or np.any((risks < 0) | (risks > 1)):
                raise ValueError("Invalid DNF probabilities")
            # Standard dry preserves the learned probabilities exactly.
            risks = np.clip(risks * sc.dnf_base_rate / 0.05, 0, 1)
        # Sample in a few vectorized strata to retain race-level scenario shocks.
        codes = rain.astype(int) * 4 + safety.astype(int) * 2 + vsc.astype(int)
        positions = np.empty((self.n_simulations, n), dtype=int)
        retired = np.empty_like(positions, dtype=bool)
        for code in np.unique(codes):
            mask = codes == code
            scale = 1 + 0.8 * bool(code & 4) + 0.35 * bool(code & 2) + 0.12 * bool(code & 1)
            positions[mask], retired[mask] = simulate_race(
                strength, np.clip(risks * (1 + 0.4 * bool(code & 4)), 0, 1),
                int(mask.sum()), temperature * scale, team_idx,
                sc.team_sigma * sc.tyre_deg_multiplier, int(rng.integers(0, 2**32)),
                race_correlation=race_correlation, strength_covariance=strength_covariance)
        result = summarize_positions(positions, retired)
        result["Abbreviation"] = drivers.get("Abbreviation", drivers.get("Driver", pd.Series(np.arange(n))))
        result["TeamName"] = drivers.get("TeamName", "Unknown")
        result["scenario"] = scenario if isinstance(scenario, str) else sc.name
        result["scenario_kind"] = ("calibrated_reference" if scenario == "standard_dry"
                                   else "heuristic_sensitivity")
        result["uncertainty_kind"] = ("laplace_strength_and_outcome_uncertainty"
                                      if strength_covariance is not None
                                      else "conditional_outcomes_not_parameter_confidence")
        result["simulation_count"] = self.n_simulations
        result["seed"] = self.random_seed
        result["pl_temperature"] = pl_temperature
        result["race_retirement_correlation"] = race_correlation
        for target, source in [("simulated_win_prob", "win_probability"),
                               ("simulated_podium_prob", "podium_probability"),
                               ("simulated_dnf_prob", "dnf_probability")]:
            result[target] = result[source]
        return result.sort_values("simulated_win_prob", ascending=False, kind="stable").reset_index(drop=True)
