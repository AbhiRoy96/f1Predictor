"""Vectorized Plackett-Luce finishing orders, including retired drivers."""

import numpy as np
import pandas as pd

from src.simulation.scenarios import RaceScenario, get_scenario
from src.utils.config import RANDOM_SEED


def simulate_race(strength, p_dnf, n_sims=20000, pl_temperature=1.0,
                  team_idx=None, team_sigma=0.0, seed=42):
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
    rng = np.random.default_rng(seed)
    n = len(strength)
    util = strength / pl_temperature + rng.gumbel(size=(n_sims, n))
    if team_idx is not None and team_sigma > 0 and n:
        team_idx = np.asarray(team_idx)
        if team_idx.shape != strength.shape or np.any(team_idx < 0):
            raise ValueError("team_idx must contain one nonnegative team index per driver")
        _, team_idx = np.unique(team_idx, return_inverse=True)
        util += rng.normal(0, team_sigma, size=(n_sims, team_idx.max() + 1))[:, team_idx]
    retired = rng.random((n_sims, n)) < p_dnf
    # Lexicographic sorting keeps even extreme strengths behind all finishers.
    retirement_order = rng.random((n_sims, n))
    order = np.lexsort((np.where(retired, retirement_order, -util), retired), axis=1)
    pos = np.empty_like(order)
    np.put_along_axis(pos, order, np.broadcast_to(np.arange(1, n + 1), order.shape), axis=1)
    return pos, retired


def summarize_positions(positions, retired):
    """Coherent unconditional probabilities; no rounding or dropped retirements."""
    n = positions.shape[1]
    result = pd.DataFrame({
        "win_probability": (positions == 1).mean(axis=0),
        "podium_probability": (positions <= min(3, n)).mean(axis=0),
        "top10_probability": (positions <= min(10, n)).mean(axis=0),
        "dnf_probability": retired.mean(axis=0),
        "expected_position": positions.mean(axis=0),
        "median_position": np.median(positions, axis=0),
        "p10_position": np.percentile(positions, 10, axis=0),
        "p90_position": np.percentile(positions, 90, axis=0),
    })
    for position in range(1, n + 1):
        result[f"prob_p{position}"] = (positions == position).mean(axis=0)
    return result


class MonteCarloRaceSimulator:
    """Scenario wrapper; legacy win-probability inputs are interpreted as PL weights."""

    def __init__(self, n_simulations=2000, random_seed=RANDOM_SEED):
        if not isinstance(n_simulations, (int, np.integer)) or n_simulations < 1:
            raise ValueError("n_simulations must be a positive integer")
        self.n_simulations = n_simulations
        self.random_seed = random_seed

    def simulate(self, drivers_df, scenario="standard_dry", win_probabilities=None,
                 strength=None, p_dnf=None, pl_temperature=1.0):
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
                sc.team_sigma * sc.tyre_deg_multiplier, int(rng.integers(0, 2**32)))
        result = summarize_positions(positions, retired)
        result["Abbreviation"] = drivers.get("Abbreviation", drivers.get("Driver", pd.Series(np.arange(n))))
        result["TeamName"] = drivers.get("TeamName", "Unknown")
        result["scenario"] = scenario if isinstance(scenario, str) else sc.name
        for target, source in [("simulated_win_prob", "win_probability"),
                               ("simulated_podium_prob", "podium_probability"),
                               ("simulated_dnf_prob", "dnf_probability")]:
            result[target] = result[source]
        return result.sort_values("simulated_win_prob", ascending=False, kind="stable").reset_index(drop=True)
