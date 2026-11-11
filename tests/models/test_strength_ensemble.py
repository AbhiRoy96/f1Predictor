"""Learned-weight, scale provenance and forecast integration tests."""

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import joblib
import numpy as np
import pandas as pd

import main as pipeline
from src.models.evaluate import run_race_walk_forward
from src.models.race_model import (
    DEFAULT_STRENGTH_MODEL,
    RacePredictor,
)
from src.models.strength_ensemble import (
    blend_distribution,
    fit_ensemble_weight,
    training_score_scale,
)
from src.simulation.race_simulator import simulate_saved_prediction
from tests.models.test_race_model import race_fixture


class EnsembleMathTests(unittest.TestCase):
    def test_scale_is_training_race_centered_rms_and_handles_constant_scores(self):
        frame = race_fixture(2, 4)
        scores = np.tile([0., 1., 2., 3.], 2)
        scale = training_score_scale(scores, frame)
        self.assertAlmostEqual(scale, np.sqrt(1.25))
        shifted = scores + np.repeat([100, -42], 4)
        self.assertAlmostEqual(training_score_scale(shifted, frame), scale)
        self.assertEqual(training_score_scale(np.ones(8), frame), 1)
        with self.assertRaisesRegex(ValueError, "finite"):
            training_score_scale(np.full(8, np.nan), frame)

    def test_weight_endpoints_and_covariance_are_mathematically_consistent(self):
        scores = np.array([[2., -1.], [0., 1.], [-2., 0.]])
        covariance = np.array([[2., -.5, -.2], [-.5, 1., -.2], [-.2, -.2, 1.]])
        for weight in (0., .3, 1.):
            mean, combined_covariance = blend_distribution(scores, covariance, weight)
            np.testing.assert_allclose(mean, weight * scores[:, 0] + (1 - weight) * scores[:, 1])
            np.testing.assert_allclose(combined_covariance, covariance * weight**2)
        with self.assertRaisesRegex(ValueError, "weight"):
            blend_distribution(scores, covariance, 1.1)
        with self.assertRaisesRegex(ValueError, "two-component"):
            blend_distribution(np.zeros((3, 3)), covariance, .5)

    def test_oof_selection_favors_the_component_with_predictive_information(self):
        frame = race_fixture(1).assign(target_position=np.arange(1, 7))
        good = np.linspace(5, -5, 6)
        examples = [{"frame": frame, "component_scores": np.column_stack([good, -good]),
                     "strength_covariance": np.zeros((6, 6)), "p_dnf": np.zeros(6)}]
        weight = fit_ensemble_weight(examples, n_simulations=300, seed=4)
        self.assertGreater(weight, .8)
        self.assertEqual(weight, fit_ensemble_weight(examples, n_simulations=300, seed=4))
        reversed_examples = [dict(examples[0], component_scores=np.column_stack([-good, good]))]
        self.assertLess(fit_ensemble_weight(reversed_examples, n_simulations=300, seed=4), .2)
        self.assertEqual(fit_ensemble_weight([]), .5)


class EnsembleIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        frame = race_fixture(8)
        cls.train = frame.loc[frame.RoundNumber.lt(8)].reset_index(drop=True)
        cls.target = frame.loc[frame.RoundNumber.eq(8)].reset_index(drop=True)
        cls.model = RacePredictor(n_estimators=5, strength_model="ensemble").fit(cls.train)

    def test_oof_weights_scaling_and_calibration_have_training_only_provenance(self):
        model = self.model
        np.testing.assert_allclose(model.blend_weights_.sum(), 1)
        self.assertTrue(((model.blend_weights_ >= 0) & (model.blend_weights_ <= 1)).all())
        self.assertEqual(model.metadata_["component_order"], ["bayesian", "xgb"])
        self.assertEqual(model.calibration_provenance_["weights_source"], "oof_final_simulation")
        self.assertEqual(model.calibration_provenance_["pl_temperature_source"], "oof_final_simulation")
        for fold in model.calibration_provenance_["folds"]:
            self.assertLess(pd.Timestamp(fold["train_max_utc"]), pd.Timestamp(fold["validation_min_utc"]))
            self.assertEqual(fold["component_scales"]["source"], "training_race_centered_rms")
            self.assertGreater(fold["component_scales"]["bayesian"], 0)
            self.assertGreater(fold["component_scales"]["xgb"], 0)
        self.assertIsNone(model.components_.xgb[-1])

    def test_inference_uses_the_same_scaled_covariance_and_frozen_scales(self):
        components = self.model.components_
        scales = (components.bayesian_scale, components.xgb_scale)
        mean, covariance = self.model._strength_distribution(self.target, components)
        scores, base_covariance = self.model._ensemble_scores(self.target, components)
        expected = blend_distribution(scores, base_covariance, self.model.blend_weights_[0])
        np.testing.assert_allclose(mean, expected[0])
        np.testing.assert_allclose(covariance, expected[1])
        altered = self.target.assign(grid_position=np.arange(6) * 1000, target_position=99999)
        self.model._ensemble_scores(altered, components)
        self.assertEqual(scales, (components.bayesian_scale, components.xgb_scale))
        poisoned = self.target.assign(target_position=99999, target_dnf=True)
        np.testing.assert_allclose(self.model._ensemble_scores(poisoned, components)[0], scores)

    def test_shuffling_training_data_is_deterministic(self):
        model = RacePredictor(n_estimators=5, strength_model="ensemble").fit(
            self.train.sample(frac=1, random_state=8))
        np.testing.assert_array_equal(self.model.blend_weights_, model.blend_weights_)
        self.assertEqual(self.model.pl_temperature_, model.pl_temperature_)
        pd.testing.assert_frame_equal(
            self.model.predict(self.target, n_simulations=100),
            model.predict(self.target, n_simulations=100))

    def test_forecast_roundtrip_replays_posterior_and_preserves_probability_constraints(self):
        for session in ("R", "Q"):
            prediction = self.model.predict(self.target.assign(SessionType=session), n_simulations=400, seed=7)
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                prediction.sample(frac=1, random_state=2).to_parquet(root / "forecast.parquet")
                replay = simulate_saved_prediction(pd.read_parquet(root / "forecast.parquet"))
                joblib.dump(self.model, root / "model.joblib")
                pd.testing.assert_frame_equal(
                    prediction, joblib.load(root / "model.joblib").predict(
                        self.target.assign(SessionType=session), n_simulations=400, seed=7))
            columns = ["win_probability", "podium_probability", "top10_probability", "expected_position",
                       *[f"prob_p{i}" for i in range(1, 7)]]
            pd.testing.assert_frame_equal(
                prediction.set_index("Abbreviation").sort_index()[columns],
                replay.set_index("Abbreviation").sort_index()[columns])
            matrix = prediction.filter(regex=r"^prob_p\d+$").to_numpy()
            np.testing.assert_allclose(matrix.sum(axis=0), 1)
            np.testing.assert_allclose(matrix.sum(axis=1), 1)
            self.assertTrue(prediction.strength_model.eq("ensemble").all())
            if session == "Q":
                self.assertTrue(prediction.dnf_probability.eq(0).all())

    def test_single_race_uses_explicit_prior_instead_of_insample_stacking(self):
        model = RacePredictor(n_estimators=3, strength_model="ensemble").fit(self.train.iloc[:6])
        np.testing.assert_array_equal(model.blend_weights_, [.5, .5])
        self.assertEqual(model.pl_temperature_, 1)
        self.assertEqual(model.calibration_provenance_["weights_source"], "fixed_equal_prior")

    def test_walk_forward_supports_ensemble(self):
        original = RacePredictor
        with patch("src.models.race_model.RacePredictor",
                   side_effect=lambda **kwargs: original(n_estimators=3, **kwargs)):
            result = run_race_walk_forward(race_fixture(6), min_train_races=3, refit_every=2,
                                          n_simulations=50, strength_model="ensemble")
        self.assertEqual(len(result["metrics"]), 15)
        self.assertTrue(result["predictions"].strength_model.eq("ensemble").all())
        self.assertTrue(np.isfinite(result["summary"].win_log_loss).all())
        self.assertNotIn("strength_covariance_row", result["baselines"])

    def test_cli_and_persistent_pipeline_select_ensemble_without_cross_loading(self):
        self.assertEqual(DEFAULT_STRENGTH_MODEL, "ensemble")
        with patch.object(pipeline, "run_pipeline") as run:
            pipeline.main(["train", "race", "2026", "Test", "--strength-model", "ensemble"])
        self.assertEqual(run.call_args.kwargs["strength_model"], "ensemble")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            feature_path = root / "features.parquet"
            pd.concat([self.train, self.target]).assign(SessionType="R").to_parquet(feature_path)
            with patch.object(pipeline, "ensure_processed_data", return_value=feature_path), patch.object(
                pipeline, "_resolve_target_event", return_value=(8, "Test")
            ), patch.object(pipeline, "validate_features_df", return_value=(True, [])), patch.object(
                pipeline, "MODELS_DIR", root / "models"
            ), patch.object(pipeline, "PREDICTIONS_DATA_DIR", root / "predictions"):
                artifact = pipeline.run_pipeline("race", 2026, "Test", action="train", strength_model="ensemble")
                self.assertEqual(artifact["metadata"]["strength_model"], "ensemble")
                with redirect_stdout(io.StringIO()):
                    prediction = pipeline.run_pipeline(
                        "race", 2026, "Test", action="predict", strength_model="ensemble", n_simulations=100)
                self.assertTrue((root / "predictions" / "predictions_race_2026_rnd8_ensemble.parquet").exists())
                self.assertAlmostEqual(prediction.win_probability.sum(), 1)
                with self.assertRaisesRegex(RuntimeError, "No matching trained model"):
                    pipeline.run_pipeline("race", 2026, "Test", action="predict", strength_model="bayesian")
