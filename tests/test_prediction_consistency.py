"""Regression coverage for calibration, reliability, uncertainty and saved forecasts."""

import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from src.data.outcomes import dnf_labels
from src.models.calibrate import fit_simulation_temperature
from src.models.evaluate import bootstrap_race_metrics
from src.models.race_model import RacePredictor, ShrinkageDNFModel, chronological_races
from src.simulation.race_simulator import (
    MonteCarloRaceSimulator, simulate_race, simulate_saved_prediction, summarize_positions,
)
from src.simulation.scenarios import get_scenario
from tests.test_race_model import race_fixture


class PredictionConsistencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frame = race_fixture()
        cls.model = RacePredictor(n_estimators=5).fit(cls.frame)

    def test_saved_forecast_replays_same_distribution_and_heuristic_scenarios(self):
        target = self.frame.iloc[-6:]
        for scenario in ("standard_dry", "wet_chaos"):
            prediction = self.model.predict(target, n_simulations=1500, seed=19, scenario=scenario)
            replay = simulate_saved_prediction(prediction.sample(frac=1, random_state=8), scenario)
            columns = ["win_probability", "podium_probability", "dnf_probability", "expected_position",
                       *[f"prob_p{i}" for i in range(1, 7)]]
            expected = prediction.set_index("Abbreviation").sort_index()[columns]
            actual = replay.set_index("Abbreviation").sort_index()[columns]
            pd.testing.assert_frame_equal(expected, actual)
        self.assertTrue(prediction.scenario_kind.eq("heuristic_sensitivity").all())
        self.assertTrue(prediction.uncertainty_kind.eq("conditional_outcomes_not_parameter_confidence").all())

    def test_qualifying_does_not_inherit_race_retirements_or_safety_car_noise(self):
        target = self.frame.iloc[-6:].assign(SessionType="Q")
        prediction = self.model.predict(target, n_simulations=500)
        self.assertTrue(prediction.base_dnf_probability.eq(0).all())
        self.assertTrue(prediction.dnf_probability.eq(0).all())
        replay = simulate_saved_prediction(prediction)
        np.testing.assert_array_equal(
            prediction.sort_values("Abbreviation").win_probability,
            replay.sort_values("Abbreviation").win_probability)
        with self.assertRaisesRegex(ValueError, "qualifying"):
            self.model.predict(target, scenario="wet_chaos")

    def test_unknown_causes_are_not_fabricated_and_components_are_exclusive(self):
        total, mechanical, collision = dnf_labels(pd.DataFrame({"Status": ["Retired", "Finished", None]}))
        self.assertEqual(total.iloc[0], 1)
        self.assertTrue(np.isnan(mechanical.iloc[0]))
        self.assertTrue(np.isnan(collision.iloc[0]))
        self.assertTrue(np.isnan(total.iloc[2]))
        risk = self.model.dnf_model_
        components = risk.predict_components(self.frame)
        np.testing.assert_allclose(sum(components.values()), risk.predict(self.frame))
        self.assertTrue((sum(components.values()) <= 1).all())
        unknown = self.frame.drop(columns=["target_dnf_mechanical", "target_dnf_collision"])
        fallback = ShrinkageDNFModel().fit(unknown).predict_components(unknown)
        self.assertTrue((fallback["mechanical"] == 0).all())
        self.assertTrue((fallback["collision"] == 0).all())
        self.assertTrue((fallback["other"] > 0).all())

    def test_known_retirements_are_excluded_from_strength_training(self):
        frame = self.frame.copy()
        model = RacePredictor(n_estimators=2)
        model.feature_columns_ = ["grid_position"]
        with patch("src.models.race_model.XGBRanker.fit", return_value=None) as fit:
            model._fit_strength(frame, chronological_races(frame))
        fitted_x, relevance = fit.call_args.args
        self.assertEqual(len(fitted_x), len(frame) - frame.target_dnf.sum())
        self.assertTrue((relevance > 0).all())
        self.assertEqual(sum(fit.call_args.kwargs["group"]), len(fitted_x))

    def test_final_calibrator_uses_retirement_and_dependence_inputs(self):
        frame = self.frame.iloc[-6:].reset_index(drop=True)
        example = dict(frame=frame, strength=np.arange(6, dtype=float), p_dnf=np.linspace(.05, .3, 6),
                       race_correlation=.3)
        original = MonteCarloRaceSimulator.simulate
        with patch("src.simulation.race_simulator.MonteCarloRaceSimulator.simulate",
                   autospec=True, side_effect=original) as simulate:
            temperature = fit_simulation_temperature([example], n_simulations=100, seed=11)
        self.assertGreaterEqual(temperature, .3)
        self.assertLessEqual(temperature, 5)
        self.assertGreater(simulate.call_count, 2)
        for call in simulate.call_args_list:
            np.testing.assert_array_equal(call.kwargs["p_dnf"], example["p_dnf"])
            self.assertEqual(call.kwargs["race_correlation"], .3)
        self.assertEqual(self.model.temperature_, 1)

    def test_finite_simulation_regularization_preserves_probability_constraints(self):
        positions = np.tile(np.arange(1, 5), (10, 1))
        summary = summarize_positions(positions, np.zeros_like(positions, dtype=bool))
        matrix = summary.filter(regex=r"^prob_p\d+$").to_numpy()
        self.assertTrue((matrix > 0).all())
        np.testing.assert_allclose(matrix.sum(axis=0), 1)
        np.testing.assert_allclose(matrix.sum(axis=1), 1)
        np.testing.assert_allclose(summary.win_probability, matrix[:, 0])
        self.assertAlmostEqual(summary.expected_position.sum(), 10)

    def test_common_retirement_shock_preserves_marginals_and_adds_dependence(self):
        risks = np.array([.15, .2, .3])
        _, independent = simulate_race(np.zeros(3), risks, 100000, seed=7)
        _, dependent = simulate_race(np.zeros(3), risks, 100000, seed=7, race_correlation=.65)
        np.testing.assert_allclose(dependent.mean(axis=0), risks, atol=.005)
        covariance = np.cov(dependent.astype(float), rowvar=False)[0, 1]
        independent_covariance = np.cov(independent.astype(float), rowvar=False)[0, 1]
        self.assertGreater(covariance, independent_covariance + .03)
        with self.assertRaises(ValueError):
            simulate_race([0], [.1], race_correlation=1)

    def test_regularization_does_not_give_guaranteed_retirees_impossible_wins(self):
        places = np.tile([4, 1, 2, 3], (20, 1))
        retired = np.tile([True, False, False, False], (20, 1))
        summary = summarize_positions(places, retired)
        self.assertEqual(summary.win_probability.iloc[0], 0)
        self.assertEqual(summary.prob_p4.iloc[0], 1)
        self.assertAlmostEqual(summary.win_probability.sum(), 1)
        np.testing.assert_allclose(summary.filter(regex=r"^prob_p\d+$").sum(axis=0), 1)

    def test_retirement_classification_uses_hazard_distance_not_uniform_order(self):
        places, retired = simulate_race([0, 0], [.9, .1], 100000, seed=6)
        both = retired.all(axis=1)
        self.assertGreater((places[both, 0] > places[both, 1]).mean(), .6)

    def test_bootstrap_uses_paired_races_and_withholds_tiny_sample_intervals(self):
        metrics = pd.DataFrame([
            dict(model=model, race_index=i, position_mae=2 + i / 10 - improvement)
            for model, improvement in (("grid", 0), ("race_model", .25)) for i in range(8)
        ])
        confidence = bootstrap_race_metrics(metrics, seed=8)
        difference = confidence.query("model == 'race_model' and reference == 'grid_difference'").iloc[0]
        self.assertAlmostEqual(difference.estimate, -.25)
        self.assertAlmostEqual(difference.ci_low, -.25)
        self.assertAlmostEqual(difference.ci_high, -.25)
        pd.testing.assert_frame_equal(confidence, bootstrap_race_metrics(metrics, seed=8))
        tiny = bootstrap_race_metrics(metrics.query("race_index < 4"))
        self.assertTrue(tiny.ci_low.isna().all())

    def test_legacy_replay_and_unknown_scenarios_fail_explicitly(self):
        with self.assertRaisesRegex(ValueError, "Regenerate"):
            simulate_saved_prediction(pd.DataFrame({"win_probability": [.6, .4]}))
        with self.assertRaisesRegex(ValueError, "Unknown scenario"):
            get_scenario("typo")


if __name__ == "__main__":
    unittest.main()
