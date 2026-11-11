"""Numerical, chronological and pipeline coverage for Laplace race strengths."""

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
from src.models.bayesian_strength import DynamicBayesianStrength, pl_derivatives
from src.models.calibrate import fit_simulation_temperature
from src.models.evaluate import run_race_walk_forward
from src.models.race_model import DEFAULT_STRENGTH_MODEL, RacePredictor
from src.simulation.race_simulator import (
    MonteCarloRaceSimulator,
    simulate_race,
    simulate_saved_prediction,
)
from tests.models.test_race_model import race_fixture


class BayesianStrengthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frame = race_fixture(9)
        cls.train = cls.frame.loc[cls.frame.RoundNumber.le(8)].reset_index(drop=True)
        cls.target = cls.frame.loc[cls.frame.RoundNumber.eq(9)].reset_index(drop=True)
        cls.model = DynamicBayesianStrength().fit(cls.train, ["grid_position"])

    def test_pl_gradient_and_hessian_match_finite_differences(self):
        scores = np.array([1.2, -.4, .7, -1.1])
        _, gradient, hessian = pl_derivatives(scores)
        epsilon = 1e-5
        perturbations = np.eye(len(scores)) * epsilon
        numeric_gradient = np.array([
            (pl_derivatives(scores + delta)[0] - pl_derivatives(scores - delta)[0]) / (2 * epsilon)
            for delta in perturbations])
        numeric_hessian = np.column_stack([
            (pl_derivatives(scores + delta)[1] - pl_derivatives(scores - delta)[1]) / (2 * epsilon)
            for delta in perturbations])
        np.testing.assert_allclose(gradient, numeric_gradient, atol=1e-8)
        np.testing.assert_allclose(hessian, numeric_hessian, atol=1e-8)
        self.assertAlmostEqual(gradient.sum(), 0)
        self.assertGreaterEqual(np.linalg.eigvalsh(hessian).min(), -1e-12)

    def test_posterior_is_finite_centered_and_positive_semidefinite(self):
        mean, covariance = self.model.posterior(self.target)
        self.assertEqual(self.model.n_updates_, 8)
        self.assertAlmostEqual(mean.sum(), 0)
        self.assertTrue(np.isfinite(mean).all())
        np.testing.assert_allclose(covariance.sum(axis=0), 0, atol=1e-12)
        self.assertGreaterEqual(np.linalg.eigvalsh(covariance).min(), -1e-12)
        self.assertGreater(mean[0], mean[-1])
        for contrasts in (self.model.driver_contrasts_, self.model.team_contrasts_):
            np.testing.assert_allclose(contrasts.sum(axis=0), 0, atol=1e-12)

    def test_time_evolution_and_regulation_reset(self):
        near = self.target.copy()
        far = self.target.assign(SessionStartUtc=self.target.SessionStartUtc + pd.Timedelta(days=140))
        self.assertGreater(np.trace(self.model.posterior(far)[1]), np.trace(self.model.posterior(near)[1]))
        reset_mean, reset_covariance = self.model._transition(
            self.model.last_start_, 2022)
        section = self.model.team_slice_
        np.testing.assert_allclose(reset_mean[section], self.model.mean_[section] * .25)
        expected = self.model.covariance_[section, section] * .25**2
        expected += np.eye(expected.shape[0]) * (1 - .25**2)
        np.testing.assert_allclose(reset_covariance[section, section], expected)

    def test_unknown_entities_pool_constructor_uncertainty(self):
        unseen = self.target.assign(Abbreviation=[f"NEW{i}" for i in range(6)], TeamName="NEW_TEAM")
        _, shared = self.model.posterior(unseen)
        _, separate = self.model.posterior(unseen.assign(TeamName=[f"NEW_TEAM{i}" for i in range(6)]))
        self.assertGreater(np.trace(separate), np.trace(shared))
        self.assertGreater(np.trace(shared), 0)

    def test_shuffling_and_prediction_labels_cannot_change_posterior(self):
        shuffled = DynamicBayesianStrength().fit(self.train.sample(frac=1, random_state=5), ["grid_position"])
        np.testing.assert_allclose(self.model.mean_, shuffled.mean_)
        mean, covariance = self.model.posterior(self.target)
        poisoned = self.target.assign(target_position=99999, target_dnf=True, target_winner=False)
        np.testing.assert_allclose(mean, self.model.posterior(poisoned)[0])
        np.testing.assert_allclose(covariance, self.model.posterior(poisoned)[1])
        order = np.array([3, 0, 5, 1, 4, 2])
        reordered_mean, reordered_covariance = self.model.posterior(self.target.iloc[order])
        np.testing.assert_allclose(reordered_mean, mean[order], atol=1e-12)
        np.testing.assert_allclose(reordered_covariance, covariance[np.ix_(order, order)], atol=1e-12)

    def test_retired_positions_do_not_train_competitive_strength(self):
        frame = self.train.assign(target_position=self.train.grid_position,
                                  target_dnf=self.train.Abbreviation.eq("D5"))
        poisoned = frame.copy()
        for indices in poisoned.groupby("RoundNumber").groups.values():
            retired = poisoned.loc[indices].index[poisoned.loc[indices].target_dnf][0]
            last_finisher = poisoned.loc[indices].loc[~poisoned.loc[indices].target_dnf].target_position.idxmax()
            poisoned.loc[[retired, last_finisher], "target_position"] = poisoned.loc[
                [last_finisher, retired], "target_position"].to_numpy()
        a = DynamicBayesianStrength().fit(frame, ["grid_position"])
        b = DynamicBayesianStrength().fit(poisoned, ["grid_position"])
        np.testing.assert_allclose(a.mean_, b.mean_, atol=1e-9)

    def test_invalid_or_nonfuture_races_fail_explicitly(self):
        with self.assertRaisesRegex(ValueError, "strictly after"):
            self.model.posterior(self.train.iloc[-6:])
        with self.assertRaisesRegex(ValueError, "one valid race"):
            self.model.posterior(pd.concat([self.target, self.train.iloc[-6:]]))
        with self.assertRaisesRegex(ValueError, "nonmissing identities"):
            self.model.posterior(self.target.assign(TeamName=None))
        with self.assertRaisesRegex(ValueError, "unique positions"):
            DynamicBayesianStrength().fit(self.train.assign(target_position=1), [])

    def test_joint_uncertainty_changes_simulated_order_distribution(self):
        mean = np.array([2., 0.])
        no_risk = np.zeros(2)
        fixed, _ = simulate_race(mean, no_risk, n_sims=30000, seed=8)
        uncertain, _ = simulate_race(mean, no_risk, n_sims=30000, seed=8,
                                     strength_covariance=np.diag([8., 8.]))
        self.assertLess((uncertain[:, 0] == 1).mean(), (fixed[:, 0] == 1).mean() - .1)
        for invalid in (np.eye(3), [[1, 2], [2, 1]], [[1, np.nan], [0, 1]]):
            with self.assertRaisesRegex(ValueError, "covariance"):
                simulate_race(mean, no_risk, strength_covariance=invalid)

    def test_laplace_update_numerical_stability_with_multiple_contexts(self):
        for seed in range(5):
            np.random.seed(seed)
            frame = race_fixture(12)
            train = frame.loc[frame.RoundNumber.lt(12)].reset_index(drop=True)
            model = DynamicBayesianStrength().fit(train, ["grid_position", "driver_race_season_avg_position"])
            self.assertEqual(model.n_updates_, 11)
            self.assertTrue(np.isfinite(model.mean_).all())
            self.assertTrue(np.isfinite(model.covariance_).all())


class BayesianIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        frame = race_fixture(7)
        cls.train = frame.loc[frame.RoundNumber.lt(7)].reset_index(drop=True)
        cls.target = frame.loc[frame.RoundNumber.eq(7)].reset_index(drop=True)
        cls.model = RacePredictor(strength_model="bayesian").fit(cls.train)

    def test_final_oof_calibration_contains_joint_posterior(self):
        for example in self.model.calibration_examples_:
            self.assertEqual(example["strength_covariance"].shape, (6, 6))
        self.assertEqual(self.model.metadata_["inference"], "sequential_laplace_gaussian")
        for fold in self.model.calibration_provenance_["folds"]:
            self.assertLess(pd.Timestamp(fold["train_max_utc"]), pd.Timestamp(fold["validation_min_utc"]))
        example = self.model.calibration_examples_[-1]
        original = MonteCarloRaceSimulator.simulate
        with patch.object(MonteCarloRaceSimulator, "simulate", autospec=True, side_effect=original) as simulate:
            fit_simulation_temperature([example], n_simulations=40)
        for call in simulate.call_args_list:
            np.testing.assert_array_equal(call.kwargs["strength_covariance"], example["strength_covariance"])

    def test_forecast_parquet_and_joblib_replay_are_exact(self):
        for scenario in ("standard_dry", "wet_chaos"):
            prediction = self.model.predict(self.target, n_simulations=500, seed=17, scenario=scenario)
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                prediction.sample(frac=1, random_state=8).to_parquet(root / "prediction.parquet")
                replay = simulate_saved_prediction(pd.read_parquet(root / "prediction.parquet"), scenario)
                joblib.dump(self.model, root / "model.joblib")
                pd.testing.assert_frame_equal(
                    prediction, joblib.load(root / "model.joblib").predict(
                        self.target, n_simulations=500, seed=17, scenario=scenario))
            columns = ["win_probability", "podium_probability", "expected_position",
                       *[f"prob_p{i}" for i in range(1, 7)]]
            pd.testing.assert_frame_equal(
                prediction.set_index("Abbreviation").sort_index()[columns],
                replay.set_index("Abbreviation").sort_index()[columns])
            matrix = prediction.filter(regex=r"^prob_p\d+$").to_numpy()
            np.testing.assert_allclose(matrix.sum(axis=0), 1)
            np.testing.assert_allclose(matrix.sum(axis=1), 1)
            self.assertTrue(prediction.uncertainty_kind.eq("laplace_strength_and_outcome_uncertainty").all())
        with self.assertRaisesRegex(ValueError, "covariance rows"):
            simulate_saved_prediction(prediction.drop(columns="strength_covariance_row"))

    def test_qualifying_is_retirement_free_and_input_order_is_preserved(self):
        target = self.target.sample(frac=1, random_state=2).assign(SessionType="Q")
        target.index = [7] * len(target)
        prediction = self.model.predict(target, n_simulations=200)
        self.assertEqual(prediction.Abbreviation.tolist(), target.Abbreviation.tolist())
        self.assertEqual(prediction.index.tolist(), target.index.tolist())
        self.assertTrue(prediction.dnf_probability.eq(0).all())
        self.assertTrue(prediction.strength_std.gt(0).all())

    def test_single_race_has_no_insample_calibration(self):
        model = RacePredictor(strength_model="bayesian").fit(self.train.iloc[:6])
        self.assertEqual(model.calibration_provenance_["n_races"], 0)
        self.assertEqual(model.pl_temperature_, 1)

    def test_walk_forward_supports_challenger_without_posterior_baselines(self):
        result = run_race_walk_forward(race_fixture(6), min_train_races=3, refit_every=2,
                                      n_simulations=50, strength_model="bayesian")
        self.assertEqual(set(result["summary"].model), {"race_model", "grid", "quali", "championship", "team"})
        self.assertEqual(len(result["metrics"]), 15)
        self.assertTrue(result["predictions"].strength_model.eq("bayesian").all())
        self.assertNotIn("strength_covariance_row", result["baselines"])
        self.assertTrue(np.isfinite(result["summary"].position_mae).all())

    def test_cli_selection_and_artifact_fingerprints_are_variant_specific(self):
        for variant in ("legacy", "bayesian"):
            with patch.object(pipeline, "run_pipeline") as run:
                pipeline.main(["train", "race", "2026", "Test", "--strength-model", variant])
            self.assertEqual(run.call_args.kwargs["strength_model"], variant)
        with patch.object(pipeline, "run_pipeline") as run:
            pipeline.main(["race", "2026", "Test"])
        self.assertEqual(run.call_args.kwargs["strength_model"], DEFAULT_STRENGTH_MODEL)
        self.assertNotEqual(pipeline._training_hash(self.train),
                            pipeline._training_hash(self.train, "bayesian"))

    def test_pipeline_trains_loads_and_persists_variant_predictions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            features = pd.concat([self.train, self.target], ignore_index=True).assign(SessionType="R")
            feature_path = root / "features.parquet"
            features.to_parquet(feature_path)
            with patch.object(pipeline, "ensure_processed_data", return_value=feature_path), patch.object(
                pipeline, "_resolve_target_event", return_value=(7, "Test")
            ), patch.object(pipeline, "validate_features_df", return_value=(True, [])), patch.object(
                pipeline, "MODELS_DIR", root / "models"
            ), patch.object(pipeline, "PREDICTIONS_DATA_DIR", root / "predictions"):
                pipeline.run_pipeline("race", 2026, "Test", action="train", strength_model="bayesian")
                with redirect_stdout(io.StringIO()):
                    prediction = pipeline.run_pipeline(
                        "race", 2026, "Test", action="predict", strength_model="bayesian", n_simulations=100)
                self.assertTrue((root / "predictions" / "predictions_race_2026_rnd7_bayesian.parquet").exists())
                self.assertTrue(prediction.strength_model.eq("bayesian").all())
                with self.assertRaisesRegex(RuntimeError, "No matching trained model"):
                    pipeline.run_pipeline("race", 2026, "Test", action="predict", strength_model="legacy")
