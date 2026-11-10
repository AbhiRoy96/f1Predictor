"""Unit tests for Monte Carlo race simulation and scenario modeling."""

import unittest
import numpy as np
import pandas as pd
from src.simulation.race_simulator import MonteCarloRaceSimulator, simulate_race
from src.simulation.scenarios import SCENARIOS, RaceScenario, get_scenario


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

    def test_retirees_keep_probability_mass(self):
        scenario = RaceScenario(name="all retired", description="test", dnf_base_rate=1.0)
        result = MonteCarloRaceSimulator(200).simulate(self.drivers, scenario)
        self.assertAlmostEqual(result.simulated_win_prob.sum(), 1.0)
        self.assertAlmostEqual(result.simulated_podium_prob.sum(), 3.0)
        self.assertTrue(np.allclose(result.filter(regex=r"^prob_p\d+$").sum(axis=1), 1))

    def test_does_not_mutate_global_rng(self):
        np.random.seed(123)
        expected = np.random.random(5)
        np.random.seed(123)
        MonteCarloRaceSimulator(20).simulate(self.drivers)
        np.testing.assert_array_equal(np.random.random(5), expected)

    def test_pl_temperature_extremes_and_input_probabilities(self):
        strength = np.log([0.6, 0.3, 0.09, 0.01])
        positions, _ = simulate_race(strength, np.zeros(4), n_sims=20000)
        np.testing.assert_allclose((positions == 1).mean(axis=0), np.exp(strength), atol=.015)
        cold, _ = simulate_race(strength, np.zeros(4), n_sims=2000, pl_temperature=.001)
        self.assertTrue((cold == np.arange(1, 5)).all())
        hot, _ = simulate_race(strength, np.zeros(4), n_sims=20000, pl_temperature=1e6)
        np.testing.assert_allclose((hot == 1).mean(axis=0), .25, atol=.015)

    def test_each_scenario_parameter_changes_samples(self):
        from dataclasses import replace

        drivers = self.drivers.copy()
        drivers["grid_position"] = [1, 2, 3, 4]
        base = RaceScenario("test", "test", safety_car_probability=0, vsc_probability=0,
                            weather_rain_probability=0, dnf_base_rate=0)
        simulator = MonteCarloRaceSimulator(1500)
        original = simulator.simulate(drivers, base)
        for field, value in [("safety_car_probability", 1), ("vsc_probability", 1),
                             ("weather_rain_probability", 1), ("dnf_base_rate", 1),
                             ("pace_variance_scale", 2), ("overtaking_difficulty", 1),
                             ("tyre_deg_multiplier", 2), ("pl_temperature", 2), ("team_sigma", 2)]:
            changed = simulator.simulate(drivers, replace(base, **{field: value}))
            a = original.set_index("Abbreviation").sort_index().expected_position
            b = changed.set_index("Abbreviation").sort_index().expected_position
            self.assertFalse(np.array_equal(a, b), field)

    def test_single_driver_empty_invalid_and_reproducibility(self):
        simulator = MonteCarloRaceSimulator(100)
        self.assertTrue(simulator.simulate(self.drivers.iloc[:0]).empty)
        one = simulator.simulate(self.drivers.iloc[:1])
        self.assertEqual(one.win_probability.iloc[0], 1)
        self.assertEqual(one.podium_probability.iloc[0], 1)
        self.assertEqual(one.expected_position.iloc[0], 1)
        a = simulator.simulate(self.drivers)
        pd.testing.assert_frame_equal(a, simulator.simulate(self.drivers))
        for strength, risks, count, temperature in [([1], [1, 2], 10, 1),
                                                   ([np.nan], [0], 10, 1),
                                                   ([1], [-.1], 10, 1),
                                                   ([1], [0], 0, 1),
                                                   ([1], [0], 10, 0)]:
            with self.assertRaises(ValueError):
                simulate_race(strength, risks, count, temperature)

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
