"""Unit tests for ML models, baselines, calibration, and evaluation metrics."""

import unittest
import numpy as np
import pandas as pd

from src.models.baselines import (
    ChampionshipLeaderBaseline,
    ConstructorStrengthBaseline,
    PolePositionBaseline,
    RecentFormBaseline,
)
from src.models.calibrate import CalibratedRacePredictor, normalize_race_probabilities
from src.models.evaluate import (
    compute_podium_metrics,
    compute_position_metrics,
    compute_winner_metrics,
    run_chronological_backtest,
)
from src.models.train_winner import create_logistic_regression_pipeline, create_random_forest_pipeline, create_xgboost_pipeline, EnsembleRaceWinnerModel


class TestBaselinesAndModels(unittest.TestCase):
    def setUp(self):
        np.random.seed(42)
        # Synthetic single-race test data
        self.single_race = pd.DataFrame([
            {"Year": 2026, "RoundNumber": 1, "Abbreviation": "VER", "TeamName": "Red Bull Racing", "driver_qualifying_season_avg_position": 1.0, "driver_race_season_points": 100.0, "driver_race_points_5": 80.0, "constructor_race_season_points": 150.0, "target_winner": 1, "target_podium": 1, "target_position": 1},
            {"Year": 2026, "RoundNumber": 1, "Abbreviation": "NOR", "TeamName": "McLaren", "driver_qualifying_season_avg_position": 2.0, "driver_race_season_points": 80.0, "driver_race_points_5": 70.0, "constructor_race_season_points": 140.0, "target_winner": 0, "target_podium": 1, "target_position": 2},
            {"Year": 2026, "RoundNumber": 1, "Abbreviation": "LEC", "TeamName": "Ferrari", "driver_qualifying_season_avg_position": 3.0, "driver_race_season_points": 60.0, "driver_race_points_5": 50.0, "constructor_race_season_points": 110.0, "target_winner": 0, "target_podium": 1, "target_position": 3},
            {"Year": 2026, "RoundNumber": 1, "Abbreviation": "HAM", "TeamName": "Ferrari", "driver_qualifying_season_avg_position": 4.0, "driver_race_season_points": 40.0, "driver_race_points_5": 30.0, "constructor_race_season_points": 110.0, "target_winner": 0, "target_podium": 0, "target_position": 4},
        ])

    def test_pole_baseline(self):
        model = PolePositionBaseline()
        model.fit(self.single_race)
        probs = model.predict_proba(self.single_race)[:, 1]
        self.assertAlmostEqual(probs.sum(), 1.0, places=4)
        self.assertEqual(probs.argmax(), 0)  # VER has best qualifying position

    def test_championship_leader_baseline(self):
        model = ChampionshipLeaderBaseline()
        model.fit(self.single_race)
        probs = model.predict_proba(self.single_race)[:, 1]
        self.assertAlmostEqual(probs.sum(), 1.0, places=4)
        self.assertEqual(probs.argmax(), 0)  # VER has highest points

    def test_recent_form_baseline(self):
        model = RecentFormBaseline()
        model.fit(self.single_race)
        probs = model.predict_proba(self.single_race)[:, 1]
        self.assertAlmostEqual(probs.sum(), 1.0, places=4)

    def test_constructor_strength_baseline(self):
        model = ConstructorStrengthBaseline()
        model.fit(self.single_race)
        probs = model.predict_proba(self.single_race)[:, 1]
        self.assertAlmostEqual(probs.sum(), 1.0, places=4)

    def test_normalize_race_probabilities(self):
        raw_p = np.array([0.5, 0.3, 0.2, 0.1])
        groups = pd.Series(["R1", "R1", "R1", "R1"])
        norm_p = normalize_race_probabilities(raw_p, groups)
        self.assertAlmostEqual(norm_p.sum(), 1.0, places=5)

    def test_calibrated_race_predictor(self):
        base = create_logistic_regression_pipeline()
        predictor = CalibratedRacePredictor(base)
        X = self.single_race[["driver_qualifying_season_avg_position", "driver_race_season_points"]]
        y = self.single_race["target_winner"]
        groups = pd.Series(["R1", "R1", "R1", "R1"])

        predictor.fit(X, y, groups=groups)
        p = predictor.predict_proba(X, groups=groups)[:, 1]
        self.assertAlmostEqual(p.sum(), 1.0, places=4)

    def test_winner_metrics(self):
        y_true = pd.Series([1, 0, 0, 0])
        y_prob = np.array([0.7, 0.15, 0.1, 0.05])
        groups = pd.Series(["R1", "R1", "R1", "R1"])
        metrics = compute_winner_metrics(y_true, y_prob, groups)
        self.assertEqual(metrics["top_1_accuracy"], 1.0)
        self.assertEqual(metrics["top_3_coverage"], 1.0)
        self.assertGreater(metrics["brier_score"], 0.0)

    def test_podium_metrics(self):
        y_true = pd.Series([1, 1, 1, 0])
        y_prob = np.array([0.9, 0.8, 0.7, 0.1])
        metrics = compute_podium_metrics(y_true, y_prob)
        self.assertEqual(metrics["podium_precision"], 1.0)
        self.assertEqual(metrics["podium_recall"], 1.0)

    def test_position_metrics(self):
        y_true = pd.Series([1, 2, 3, 4])
        y_pred = np.array([1.1, 2.0, 3.2, 3.9])
        metrics = compute_position_metrics(y_true, y_pred)
        self.assertLess(metrics["position_mae"], 0.5)
        self.assertGreater(metrics["spearman_rank_correlation"], 0.9)


if __name__ == "__main__":
    unittest.main()
