"""Small offline workload through feature engineering, training and serving."""

import io
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

import main as pipeline
from src.features.engineer_features import build_features


def race_history(rounds=10, drivers=6):
    rows = []
    for race in range(1, rounds + 1):
        start = pd.Timestamp("2024-01-01", tz="UTC") + pd.Timedelta(days=7 * race)
        for session, offset in (("FP2", -2), ("Q", -1), ("R", 0)):
            for driver in range(drivers):
                position = (driver + race) % drivers + 1
                rows.append(dict(Year=2024, RoundNumber=race, EventName=f"Round {race}",
                                 CircuitName="Test Circuit", SessionType=session,
                                 SessionStartUtc=start + pd.Timedelta(days=offset),
                                 Abbreviation=f"D{driver}", FullName=f"Driver {driver}",
                                 TeamName=f"Team {driver // 2}", Position=position,
                                 GridPosition=driver + 1, Points=max(0, 10 - position),
                                 Status="Finished", ClassifiedPosition=str(position),
                                 Q1=pd.Timedelta(seconds=90 + driver / 10)))
    return pd.DataFrame(rows)


class EndToEndImprovementsTests(unittest.TestCase):
    def test_train_predict_cache_and_holdout_leave_production_unchanged(self):
        features = build_features(race_history(), stage="post_quali")["R"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            feature_path = root / "race_features.parquet"
            features.to_parquet(feature_path, index=False)
            models = root / "models"
            with patch.object(pipeline, "ensure_processed_data", return_value=feature_path), patch.object(
                pipeline, "_resolve_target_event", return_value=(10, "Round 10")
            ), patch.object(pipeline, "MODELS_DIR", models), patch("main.PROCESSED_DATA_DIR", root), patch(
                "main.PREDICTIONS_DATA_DIR", root / "predictions"
            ), redirect_stdout(io.StringIO()) as output:
                artifact = pipeline.run_pipeline("race", 2024, "Round 10", action="train")
                production = {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in models.rglob("*") if p.is_file()}
                predicted = pipeline.run_pipeline("race", 2024, "Round 10", action="predict", n_simulations=1000)
                repeated = pipeline.run_pipeline("race", 2024, "Round 10", action="predict", n_simulations=1000)
                held_out = pipeline.run_pipeline("race", 2024, "Round 10", measure_variation=True, n_simulations=1000)
            self.assertTrue(artifact["metadata"]["model_version"])
            self.assertEqual(len(list(models.glob("*/metadata.json"))), 1)
            self.assertEqual(production, {p: (p.stat().st_mtime_ns, p.read_bytes())
                                          for p in models.rglob("*") if p.is_file()})
            pd.testing.assert_series_equal(predicted.win_probability, repeated.win_probability)
            for predictions in (predicted, held_out):
                self.assertAlmostEqual(predictions.win_probability.sum(), 1, places=12)
                self.assertAlmostEqual(predictions.podium_probability.sum(), 3, places=12)
                self.assertTrue((predictions.win_probability <= predictions.podium_probability).all())
                self.assertTrue((predictions.podium_probability <= predictions.top10_probability).all())
                self.assertTrue(np.isfinite(predictions.expected_position).all())
                self.assertEqual(predictions.training_races.unique().tolist(), [9])
            self.assertIn("RACE PREDICTIONS", output.getvalue())
            saved = pd.read_parquet(root / "predictions/predictions_race_2024_rnd10_post_quali.parquet")
            self.assertIn("position_error", saved)
            self.assertTrue(saved.variation_measured.all())


if __name__ == "__main__":
    unittest.main()
