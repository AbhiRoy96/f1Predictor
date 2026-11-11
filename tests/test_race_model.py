"""Small deterministic integration tests for the unified race model."""

from pathlib import Path
import tempfile
import unittest

import joblib
import numpy as np
import pandas as pd

from src.models.calibrate import fit_pl_temperature, fit_temperature, pl_nll
from src.models.race_model import RacePredictor, ShrinkageDNFModel, chronological_races, dnf_labels, train_and_save_race_model


def race_fixture(n_races=8, field_size=6):
    rng = np.random.default_rng(42)
    frames = []
    for race in range(1, n_races + 1):
        grid = np.arange(1, field_size + 1)
        order = np.argsort(grid + rng.normal(0, 1.7, field_size))
        positions = np.empty(field_size, dtype=int)
        positions[order] = np.arange(1, field_size + 1)
        frames.append(pd.DataFrame({
            "Year": 2026, "RoundNumber": race,
            "SessionStartUtc": pd.Timestamp("2026-01-01", tz="UTC") + pd.Timedelta(days=7 * race),
            "Abbreviation": [f"D{i}" for i in range(field_size)],
            "TeamName": [f"T{i // 2}" for i in range(field_size)],
            "stage": "post_quali", "grid_position": grid, "quali_position": grid,
            "driver_race_season_points": (field_size - grid) * race * 3,
            "constructor_race_season_points": (field_size - grid // 2) * race * 5,
            "driver_race_season_avg_position": grid.astype(float),
            "all_missing_position": np.nan, "target_position": positions,
            "target_winner": positions == 1, "target_podium": positions <= 3,
            "target_dnf": positions == field_size if race % 3 == 0 else False,
            "target_dnf_mechanical": positions == field_size if race % 3 == 0 else False,
            "target_dnf_collision": False,
            "history_source_max_utc": 123456789, "target_future": positions,
        }))
    return pd.concat(frames, ignore_index=True)


class TestRaceModel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frame = race_fixture()
        cls.model = RacePredictor(n_estimators=8).fit(cls.frame)

    def test_probabilities_consistent_and_original_order(self):
        frame = self.frame[self.frame.RoundNumber.eq(8)].sample(frac=1, random_state=8)
        frame.index = [7] * len(frame)
        prediction = self.model.predict(frame, n_simulations=1000)
        self.assertEqual(prediction.Abbreviation.tolist(), frame.Abbreviation.tolist())
        self.assertEqual(prediction.index.tolist(), frame.index.tolist())
        self.assertAlmostEqual(prediction.win_probability.sum(), 1, places=12)
        self.assertAlmostEqual(prediction.podium_probability.sum(), 3, places=12)
        self.assertTrue((prediction.win_probability <= prediction.podium_probability).all())
        self.assertTrue((prediction.podium_probability <= prediction.top10_probability).all())
        matrix = prediction.filter(regex=r"^prob_p\d+$")
        np.testing.assert_allclose(matrix.sum(axis=1), 1)
        np.testing.assert_allclose(matrix.sum(axis=0), 1)
        self.assertFalse(prediction.select_dtypes(include="number").isna().any().any())
        self.assertTrue((prediction.p10_position <= prediction.median_position).all())
        self.assertTrue((prediction.median_position <= prediction.p90_position).all())
        pd.testing.assert_frame_equal(prediction, self.model.predict(frame, n_simulations=1000))

    def test_calibration_oof(self):
        provenance = self.model.calibration_provenance_
        self.assertEqual(provenance["method"], "expanding_race_oof")
        self.assertGreater(provenance["n_races"], 0)
        for fold in provenance["folds"]:
            self.assertFalse(set(fold["train_groups"]) & set(fold["validation_groups"]))
            self.assertLess(pd.Timestamp(fold["train_max_utc"]), pd.Timestamp(fold["validation_min_utc"]))
        self.assertEqual(provenance["weights_source"], "oof")
        self.assertEqual(provenance["pl_temperature_source"], "oof_final_simulation")
        self.assertEqual(provenance["objective"], "final_win_plus_podium_log_loss")
        self.assertEqual(self.model.temperature_, 1.0)
        self.assertAlmostEqual(sum(self.model.blend_weights_), 1)
        self.assertGreaterEqual(self.model.temperature_, 0.3)
        self.assertLessEqual(self.model.pl_temperature_, 5)

    def test_features_exclude_labels_time_provenance(self):
        self.assertIn("grid_position", self.model.feature_columns_)
        self.assertFalse(any(c.startswith("target_") or "utc" in c.lower() for c in self.model.feature_columns_))
        self.assertNotIn("Year", self.model.feature_columns_)
        self.assertNotIn("RoundNumber", self.model.feature_columns_)
        self.assertIn("all_missing_position", self.model.feature_columns_)
        index = self.model.feature_columns_.index("all_missing_position")
        self.assertEqual(self.model.components_[0].statistics_[index], 3.5)

    def test_rookie_and_new_team(self):
        frame = self.frame.iloc[-6:].copy()
        frame["Abbreviation"] = [f"NEW{i}" for i in range(6)]
        frame["TeamName"] = "New team"
        frame["driver_race_season_avg_position"] = np.nan
        risks = self.model.dnf_model_.predict_components(frame)
        for kind, values in risks.items():
            np.testing.assert_allclose(values, self.model.dnf_model_.models_[kind]["prior"])
        prediction = self.model.predict(frame, n_simulations=300)
        self.assertTrue(np.isfinite(prediction.expected_position).all())
        imputer = self.model.components_[0]
        column = self.model.feature_columns_.index("driver_race_season_avg_position")
        self.assertGreater(imputer.statistics_[column], 0)

    def test_small_sample_uses_default_not_insample_calibration(self):
        model = RacePredictor(n_estimators=3).fit(self.frame.iloc[:6])
        self.assertEqual(model.calibration_provenance_["n_races"], 0)
        self.assertEqual(model.temperature_, 1)
        self.assertEqual(model.pl_temperature_, 1)

    def test_training_order_and_future_labels_do_not_enter_features(self):
        shuffled = self.frame.sample(frac=1, random_state=18)
        model = RacePredictor(n_estimators=8).fit(shuffled)
        a = self.model.predict(self.frame.iloc[-6:], n_simulations=100)
        b = model.predict(self.frame.iloc[-6:], n_simulations=100)
        np.testing.assert_allclose(a.strength, b.strength)
        poisoned = self.frame.iloc[-6:].copy()
        poisoned["target_position"] = 99999
        pd.testing.assert_frame_equal(a, self.model.predict(poisoned, n_simulations=100))

    def test_invalid_races_rejected(self):
        frame = self.frame.copy()
        frame.loc[0, "target_position"] = frame.loc[1, "target_position"]
        with self.assertRaisesRegex(ValueError, "unique finishing"):
            RacePredictor(n_estimators=2).fit(frame)
        frame = self.frame.copy()
        frame.loc[0, "SessionStartUtc"] = pd.NaT
        with self.assertRaisesRegex(ValueError, "SessionStartUtc"):
            chronological_races(frame)

    def test_dnf_shrinkage_and_status(self):
        frame = self.frame.copy()
        frame["target_dnf_mechanical"] = frame.TeamName.eq("T0").astype(int)
        model = ShrinkageDNFModel().fit(frame)
        values = model.predict_components(frame)["mechanical"]
        self.assertGreater(values[0], values[4])
        self.assertGreater(values[0], 0)
        self.assertLess(values[0], 1)
        labels = dnf_labels(pd.DataFrame({"Status": ["Finished", "+1 Lap", "Engine", "Collision", None]}))
        np.testing.assert_allclose(labels[0].iloc[:4], [0, 0, 1, 1])
        np.testing.assert_allclose(labels[1].iloc[:4], [0, 0, 1, 0])
        np.testing.assert_allclose(labels[2].iloc[:4], [0, 0, 0, 1])
        self.assertTrue(np.isnan(labels[0].iloc[4]))
        classified = dnf_labels(pd.DataFrame({"ClassifiedPosition": ["R", "1", None]}))[0]
        self.assertEqual(classified.iloc[0], 1)
        self.assertTrue(classified.iloc[1:].isna().all())

    def test_total_only_dnf_labels_and_context_model(self):
        frame = self.frame.drop(columns=["target_dnf_mechanical", "target_dnf_collision"]).copy()
        frame["target_dnf"] = frame.grid_position.ge(5).astype(int)
        model = ShrinkageDNFModel().fit(frame)
        self.assertIsNotNone(model.total_fallback_)
        risks = model.predict(frame)
        self.assertGreater(risks[5], risks[0])
        self.assertTrue(((risks > 0) & (risks < 1)).all())
        frame["target_dnf_mechanical"] = frame.target_dnf
        frame["target_dnf_collision"] = 0
        model = ShrinkageDNFModel().fit(frame)
        self.assertIsNotNone(model.models_["mechanical"]["context"])
        self.assertTrue(np.isfinite(model.predict(frame)).all())

    def test_temperature_helpers_use_provided_scores(self):
        scores = [np.array([3., 1., 0.]), np.array([4., 1., 0.])]
        hot = fit_temperature(scores, [2, 2])
        cold = fit_temperature(scores, [0, 0])
        self.assertGreater(hot, cold)
        self.assertGreater(pl_nll(scores[0], [2, 1, 0], 0.3), pl_nll(scores[0], [2, 1, 0], 5))
        self.assertGreater(fit_pl_temperature(scores, [[2, 1, 0]] * 2), 4)

    def test_artifact_roundtrip(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as directory:
            feature_path = Path(directory) / "features.parquet"
            self.frame.to_parquet(feature_path)
            result = train_and_save_race_model(feature_path, directory)
            artifact = joblib.load(result["model_path"])
            self.assertEqual(set(artifact), {"model", "feature_columns", "metadata"})
            self.assertEqual(artifact["metadata"], result["metadata"])
            frame = self.frame.iloc[-6:]
            prediction = artifact["model"].predict(frame, 100)
            self.assertAlmostEqual(prediction.win_probability.sum(), 1)
            pd.testing.assert_frame_equal(prediction, joblib.load(result["model_path"])["model"].predict(frame, 100))


if __name__ == "__main__":
    unittest.main()
