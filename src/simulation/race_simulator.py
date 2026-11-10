"""Monte Carlo race simulation engine for scenario analysis and uncertainty modeling."""

import argparse
from typing import Dict, List, Optional, Union
import numpy as np
import pandas as pd
from src.simulation.scenarios import RaceScenario, get_scenario
from src.utils.config import RANDOM_SEED
from src.utils.logging import setup_logger

logger = setup_logger(__name__)


class MonteCarloRaceSimulator:
    """Simulates Grand Prix outcomes using stochastic race dynamics and scenario modifiers."""

    def __init__(self, n_simulations: int = 2000, random_seed: int = RANDOM_SEED):
        self.n_simulations = n_simulations
        self.random_seed = random_seed

    def simulate(
        self,
        drivers_df: pd.DataFrame,
        scenario: Union[str, RaceScenario] = "standard_dry",
        win_probabilities: Optional[np.ndarray] = None,
    ) -> pd.DataFrame:
        """Run Monte Carlo simulation for a grid of drivers.

        drivers_df must contain:
        - Abbreviation (or Driver)
        - TeamName
        Optional:
        - driver_qualifying_season_avg_position / grid_position
        - driver_race_points_5
        """
        np.random.seed(self.random_seed)
        sc = get_scenario(scenario) if isinstance(scenario, str) else scenario
        n_drivers = len(drivers_df)
        if n_drivers == 0:
            return pd.DataFrame()

        drivers = drivers_df.copy().reset_index(drop=True)
        driver_ids = drivers["Abbreviation"].values if "Abbreviation" in drivers.columns else drivers.index.values

        # Base race pace capability (lower score = faster pace)
        if win_probabilities is not None and len(win_probabilities) == n_drivers:
            # Map high win probability to faster baseline pace
            base_pace = -10.0 * np.log(np.clip(win_probabilities, 1e-4, 1.0))
        elif "driver_qualifying_season_avg_position" in drivers.columns:
            base_pace = drivers["driver_qualifying_season_avg_position"].fillna(12.0).values.astype(float)
        else:
            base_pace = np.linspace(1.0, float(n_drivers), n_drivers)

        win_counts = np.zeros(n_drivers, dtype=int)
        podium_counts = np.zeros(n_drivers, dtype=int)
        position_matrix = np.zeros((n_drivers, n_drivers), dtype=int)  # [driver, position]
        dnf_counts = np.zeros(n_drivers, dtype=int)
        finish_positions = [[] for _ in range(n_drivers)]

        base_std = 2.0 * sc.pace_variance_scale

        for sim in range(self.n_simulations):
            # 1. Pace sampling
            pace_noise = np.random.normal(0, base_std, n_drivers)
            total_time = base_pace + pace_noise

            # 2. Safety car intervention compression
            if np.random.rand() < sc.safety_car_probability:
                # SC compresses gaps by 50%
                mean_time = np.mean(total_time)
                total_time = mean_time + (total_time - mean_time) * 0.5

            # 3. Mechanical & incident DNF sampling
            dnf_mask = np.random.rand(n_drivers) < sc.dnf_base_rate
            for i in range(n_drivers):
                if dnf_mask[i]:
                    dnf_counts[i] += 1
                    total_time[i] = 1e9  # Retired

            # 4. Finishing order calculation
            finish_order = np.argsort(total_time)

            for pos, driver_idx in enumerate(finish_order):
                if not dnf_mask[driver_idx]:
                    finish_positions[driver_idx].append(pos + 1)
                    if pos < n_drivers:
                        position_matrix[driver_idx, pos] += 1
                    if pos == 0:
                        win_counts[driver_idx] += 1
                    if pos < 3:
                        podium_counts[driver_idx] += 1

        results = []
        for i, driver_id in enumerate(driver_ids):
            driver_row = drivers.iloc[i]
            win_p = float(win_counts[i] / self.n_simulations)
            podium_p = float(podium_counts[i] / self.n_simulations)
            dnf_p = float(dnf_counts[i] / self.n_simulations)
            avg_pos = float(np.mean(finish_positions[i])) if finish_positions[i] else float(n_drivers)

            # Top 3 positions distribution
            p1_p = float(position_matrix[i, 0] / self.n_simulations)
            p2_p = float(position_matrix[i, 1] / self.n_simulations) if n_drivers > 1 else 0.0
            p3_p = float(position_matrix[i, 2] / self.n_simulations) if n_drivers > 2 else 0.0

            results.append({
                "Abbreviation": driver_id,
                "TeamName": driver_row.get("TeamName", "Unknown"),
                "scenario": sc.name,
                "simulated_win_prob": round(win_p, 4),
                "simulated_podium_prob": round(podium_p, 4),
                "simulated_dnf_prob": round(dnf_p, 4),
                "expected_position": round(avg_pos, 2),
                "prob_p1": round(p1_p, 4),
                "prob_p2": round(p2_p, 4),
                "prob_p3": round(p3_p, 4),
            })

        sim_df = pd.DataFrame(results).sort_values("simulated_win_prob", ascending=False).reset_index(drop=True)
        return sim_df


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Monte Carlo F1 race simulation.")
    parser.add_argument("--scenario", type=str, default="standard_dry", help="Scenario name")
    parser.add_argument("--sims", type=int, default=2000, help="Number of simulations")
    args = parser.parse_args()

    # Quick demo grid
    demo_grid = pd.DataFrame([
        {"Abbreviation": "VER", "TeamName": "Red Bull Racing", "driver_qualifying_season_avg_position": 1.5},
        {"Abbreviation": "NOR", "TeamName": "McLaren", "driver_qualifying_season_avg_position": 2.2},
        {"Abbreviation": "LEC", "TeamName": "Ferrari", "driver_qualifying_season_avg_position": 3.0},
        {"Abbreviation": "HAM", "TeamName": "Ferrari", "driver_qualifying_season_avg_position": 4.5},
        {"Abbreviation": "PIA", "TeamName": "McLaren", "driver_qualifying_season_avg_position": 4.8},
        {"Abbreviation": "RUS", "TeamName": "Mercedes", "driver_qualifying_season_avg_position": 5.2},
    ])

    sim = MonteCarloRaceSimulator(n_simulations=args.sims)
    out = sim.simulate(demo_grid, scenario=args.scenario)
    print(out.to_string(index=False))
