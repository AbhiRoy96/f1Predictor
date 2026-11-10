"""Unit tests for Monte Carlo race simulation and scenario modeling."""

import unittest
import numpy as np
import pandas as pd
from src.simulation.race_simulator import MonteCarloRaceSimulator
from src.simulation.scenarios import SCENARIOS, get_scenario


class TestRaceSimulation(unittest.TestCase):
    def setUp(self):
        self.drivers = pd.DataFrame([
            {"Abbreviation": "VER", "TeamName": "Red Bull Racing", "driver_qualifying_season_avg_position": 1.0},
            {"Abbreviation": "NOR", "TeamName": "McLaren", "driver_qualifying_season_avg_position": 2.0},
            {"Abbreviation": "LEC", "TeamName": "Ferrari", "driver_qualifying_season_avg_position": 3.0},
            {"Abbreviation": "HAM", "TeamName": "Ferrari", "driver_qualifying_season_avg_position": 4.0},
        ])

    def test_scenarios_retrieval(self):
        self.assertIn("standard_dry", SCENARIOS)
        self.assertIn("wet_chaos", SCENARIOS)
        sc = get_scenario("wet_chaos")
        self.assertEqual(sc.name, "Wet Weather Chaos")
        self.assertGreater(sc.safety_car_probability, 0.5)

    def test_simulation_probabilities_sum_to_one(self):
        sim = MonteCarloRaceSimulator(n_simulations=500, random_seed=42)
        results = sim.simulate(self.drivers, scenario="standard_dry")

        self.assertEqual(len(results), 4)
        total_win_prob = results["simulated_win_prob"].sum()
        # Sum of simulated win probabilities should be ~1.0
        self.assertAlmostEqual(total_win_prob, 1.0, places=2)

    def test_simulation_with_explicit_win_probabilities(self):
        sim = MonteCarloRaceSimulator(n_simulations=500, random_seed=42)
        explicit_probs = np.array([0.70, 0.20, 0.08, 0.02])
        results = sim.simulate(self.drivers, scenario="standard_dry", win_probabilities=explicit_probs)

        # Fastest driver with 70% probability should win the most simulations
        top_winner = results.iloc[0]
        self.assertEqual(top_winner["Abbreviation"], "VER")
        self.assertGreater(top_winner["simulated_win_prob"], 0.4)


if __name__ == "__main__":
    unittest.main()
