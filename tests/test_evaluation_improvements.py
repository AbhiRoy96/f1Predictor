"""Past-only folds, baseline parity and complete evaluation reports."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, ClassifierMixin

from src.models.calibrate import CalibratedRacePredictor
from src.models.evaluate import reliability, run_chronological_backtest, run_race_walk_forward, save_evaluation_reports
from src.models.race_model import RacePredictor
from src.models.train_winner import create_logistic_regression_pipeline
from tests.test_race_model import race_fixture


class GroupCheckingModel:
    def fit(self, x, y, groups=None):
        if groups is None or not x.isna().any().any():
            raise AssertionError("Expected race groups and original NaNs at training")
        return self

    @staticmethod
    def predict_proba(x, groups=None):
        if groups is None or not x.isna().any().any():
            raise AssertionError("Expected race groups and original NaNs at prediction")
        counts = groups.groupby(groups).transform("size").to_numpy()
        p = 1 / counts
        return np.column_stack([1 - p, p])


class MemorizingEstimator(ClassifierMixin, BaseEstimator):
    """Detect calibration accidentally scoring training rows, without a model mock."""

    def fit(self, x, y):
        self.seen_ = set(x["row_id"])
        self.classes_ = np.array([0, 1])
        return self

    def decision_function(self, x):
        if set(x.row_id) & self.seen_:
            raise AssertionError("Calibration scored in-sample rows")
        return -x["pace"].to_numpy(float)


class TestEvaluationImprovements(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frame = race_fixture(7)
        # Exercise the actual pipeline on a few races/trees before a full historical run.
        original = RacePredictor
        with patch("src.models.race_model.RacePredictor", side_effect=lambda: original(n_estimators=5)):
            cls.result = run_race_walk_forward(cls.frame.sample(frac=1, random_state=5),
                                              min_train_races=3, refit_every=2, n_simulations=300)

    def test_shared_folds_and_refit_cadence(self):
        result = self.result
        self.assertEqual(len(result["folds"]), 2)
        self.assertEqual(result["folds"].n_train_races.tolist(), [3, 5])
        expected = set(result["predictions"].race_index)
        for name, frame in result["baselines"].groupby("model"):
            self.assertIn(name, ["grid", "quali", "championship", "team"])
            self.assertEqual(set(frame.race_index), expected)
            for race_index, race in frame.groupby("race_index"):
                self.assertAlmostEqual(race.win_probability.sum(), 1)
                self.assertAlmostEqual(race.podium_probability.sum(), 3)
                self.assertEqual(race.fold.iloc[0], result["predictions"].query("race_index == @race_index").fold.iloc[0])
        for _, fold in result["folds"].iterrows():
            self.assertLess(pd.Timestamp(fold.train_max_utc), pd.Timestamp(fold.test_min_utc))
            for calibration in fold.baseline_calibration.values():
                self.assertEqual(calibration["train_races"], fold.n_train_races)
                self.assertEqual(calibration["training_end"], fold.train_max_utc)

    def test_complete_metrics_reliability_and_bias(self):
        result = self.result
        expected = {"win_log_loss", "win_brier", "top_1_accuracy", "top_3_coverage", "podium_brier",
                    "podium_log_loss", "top_3_overlap", "spearman", "kendall_tau", "finisher_pl_nll_diagnostic",
                    "position_mae", "position_median_absolute_error", "position_error_std", "dnf_brier",
                    "dnf_constant_brier", "dnf_brier_skill",
                    "position_log_loss", "position_rps", "position_interval_80_coverage"}
        self.assertTrue(expected.issubset(result["metrics"].columns))
        self.assertEqual(len(result["metrics"]), 20)
        self.assertEqual(set(result["summary"].model), {"race_model", "grid", "quali", "championship", "team"})
        self.assertTrue(np.isfinite(result["metrics"][list(expected)]).all().all())
        self.assertNotIn("pl_log_likelihood", result["metrics"])
        self.assertNotIn("position_signed_error", result["metrics"])
        self.assertTrue(result["confidence"].ci_low.isna().all())
        for target in ["win", "podium", "top10", "dnf"]:
            table = result[f"reliability_{target}"]
            self.assertEqual(set(table.model), {"race_model", "grid", "quali", "championship", "team"})
            self.assertEqual(table.n.sum(), 5 * 4 * 6)
        for name in ["driver_bias", "team_bias"]:
            self.assertIn("rolling_signed_error", result[name])
            self.assertFalse(result[name].empty)

    def test_future_results_do_not_change_first_prediction(self):
        frame = self.frame.copy()
        future = frame.RoundNumber >= 6
        frame.loc[future, "target_position"] = 7 - frame.loc[future, "target_position"]
        with patch("src.models.race_model.RacePredictor", side_effect=lambda: RacePredictor(n_estimators=5)):
            result = run_race_walk_forward(frame, min_train_races=3, refit_every=2, n_simulations=300)
        columns = ["Abbreviation", "win_probability", "podium_probability", "expected_position"]
        a = self.result["predictions"].query("race_index == 3")[columns].sort_values("Abbreviation").reset_index(drop=True)
        b = result["predictions"].query("race_index == 3")[columns].sort_values("Abbreviation").reset_index(drop=True)
        pd.testing.assert_frame_equal(a, b)
        columns = ["model", "Abbreviation", "win_probability", "podium_probability", "expected_position"]
        a = self.result["baselines"].query("race_index == 3")[columns].sort_values(["model", "Abbreviation"]).reset_index(drop=True)
        b = result["baselines"].query("race_index == 3")[columns].sort_values(["model", "Abbreviation"]).reset_index(drop=True)
        pd.testing.assert_frame_equal(a, b)
        self.assertEqual(self.result["folds"].baseline_calibration.iloc[0], result["folds"].baseline_calibration.iloc[0])

    def test_dnf_skill_uses_training_rate_and_held_out_squared_errors(self):
        for _, race in self.result["predictions"].groupby("race_index"):
            training_rounds = self.result["folds"].query("fold == @race.fold.iloc[0]").n_train_races.iloc[0]
            expected_rate = self.frame.loc[self.frame.RoundNumber.le(training_rounds), "target_dnf"].mean()
            np.testing.assert_allclose(race.training_dnf_base_rate, expected_rate)
            constant_brier = ((race.target_dnf - expected_rate) ** 2).mean()
            actual = self.result["metrics"].query("model == 'race_model' and race_index == @race.race_index.iloc[0]").iloc[0]
            self.assertAlmostEqual(actual.dnf_constant_brier, constant_brier)
            self.assertAlmostEqual(actual.dnf_brier_skill, 1 - actual.dnf_brier / constant_brier)
        summary = self.result["summary"]
        np.testing.assert_allclose(summary.dnf_brier_skill, 1 - summary.dnf_brier / summary.dnf_constant_brier)

    def test_no_production_artifact_writes_and_opt_in_reports(self):
        root = Path(__file__).resolve().parents[1]
        before = {p: p.stat().st_mtime_ns for p in (root / "models").iterdir() if p.is_file()}
        with patch("src.models.race_model.RacePredictor", side_effect=lambda: RacePredictor(n_estimators=5)):
            run_race_walk_forward(self.frame.iloc[:18], min_train_races=2, n_simulations=20)
        self.assertEqual(before, {p: p.stat().st_mtime_ns for p in (root / "models").iterdir() if p.is_file()})
        with tempfile.TemporaryDirectory(dir=root) as directory:
            paths = save_evaluation_reports(self.result, directory)
            self.assertTrue(paths["summary"].is_file())
            self.assertEqual(len(pd.read_csv(paths["metrics"])), len(self.result["metrics"]))

    def test_empty_walk_forward_and_unknown_outcomes(self):
        result = run_race_walk_forward(self.frame, min_train_races=99)
        self.assertTrue(result["metrics"].empty)
        table = reliability([0., .5, 1.], [0, np.nan, 1])
        self.assertEqual(table.n.sum(), 2)
        self.assertTrue(table.hit_rate.between(0, 1).all())
        with self.assertRaises(ValueError):
            run_race_walk_forward(self.frame, refit_every=0)

    def test_legacy_backtest_preserves_missing_values_and_groups(self):
        frame = self.frame.copy()
        frame["Year"] = np.where(frame.RoundNumber <= 3, 2025, 2026)
        result = run_chronological_backtest(frame, GroupCheckingModel, ["grid_position", "all_missing_position"], min_train_seasons=1)
        self.assertEqual(result["folds"][0]["num_races_evaluated"], 4)
        for _, race in result["predictions"].groupby(["Year", "RoundNumber"]):
            self.assertAlmostEqual(race.pred_prob.sum(), 1)

    def test_legacy_calibration_scores_held_out_rows_only(self):
        frame = self.frame.copy()
        x = pd.DataFrame({"row_id": np.arange(len(frame)), "pace": frame.grid_position})
        groups = frame.Year.astype(str) + "_" + frame.RoundNumber.astype(str)
        model = CalibratedRacePredictor(MemorizingEstimator()).fit(x, frame.target_winner, groups)
        self.assertGreater(model.calibration_provenance_["n_races"], 0)
        for fold in model.calibration_provenance_["folds"]:
            self.assertFalse(set(fold["train_groups"]) & set(fold["validation_groups"]))
        real = CalibratedRacePredictor(create_logistic_regression_pipeline()).fit(
            frame[["grid_position"]], frame.target_winner, groups)
        probabilities = real.predict_proba(frame[["grid_position"]], groups)[:, 1]
        for group in groups.unique():
            self.assertAlmostEqual(probabilities[groups.eq(group)].sum(), 1)


if __name__ == "__main__":
    unittest.main()
