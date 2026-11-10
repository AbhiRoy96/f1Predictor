import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import joblib

import main as pipeline


class OrchestrationImprovementsTests(unittest.TestCase):
    def test_upcoming_target_has_known_entrants_but_no_outcomes(self):
        schedule = pd.DataFrame([dict(EventName="Test", RoundNumber=1, Session1="Qualifying",
                                      Session1DateUtc="2026-03-01T10:00:00Z", Session2="Race",
                                      Session2DateUtc="2026-03-02T10:00:00Z")])
        results = pd.DataFrame([dict(Year=2026, RoundNumber=1, SessionType="Q",
                                     SessionStartUtc=pd.Timestamp("2026-03-01T10:00:00Z"),
                                     Abbreviation="NEW", FullName="New Driver", TeamName="Audi",
                                     Position=1, Points=0, Status="Finished", GridPosition=1)])
        with tempfile.TemporaryDirectory() as directory:
            schedule.to_parquet(Path(directory) / "2026_schedule.parquet", index=False)
            with patch("main.RAW_DATA_DIR", Path(directory)):
                combined = pipeline._add_scheduled_target(results, "race", 2026, "Test")
        target = combined.loc[combined.SessionType.eq("R")].iloc[0]
        self.assertEqual(target.Abbreviation, "NEW")
        self.assertEqual(target.FullName, "New Driver")
        self.assertEqual(target.SessionStartUtc, pd.Timestamp("2026-03-02T10:00:00Z"))
        self.assertTrue(pd.isna(target.Position))
        self.assertTrue(pd.isna(target.Status))
        self.assertTrue(pd.isna(target.Points))

    def test_inconsistent_predictions_cannot_be_saved(self):
        predictions = pd.DataFrame(dict(win_probability=[0.6, 0.4],
                                          podium_probability=[0.5, 0.8],
                                          top10_probability=[1.0, 1.0]))
        with self.assertRaisesRegex(ValueError, "Inconsistent"):
            pipeline._validate_prediction_probabilities(predictions)

    def test_backtest_trainer_artifacts_are_temporary(self):
        features = pd.DataFrame({"value": [1.0]})
        locations = []

        def save_artifact(feature_path, output_dir):
            locations.append(Path(output_dir))
            self.assertEqual(pd.read_parquet(feature_path).value.tolist(), [1.0])
            model_path = Path(output_dir) / "race.joblib"
            joblib.dump({"model": "temporary", "feature_columns": ["value"]}, model_path)
            return {"model_path": model_path}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            models = root / "models"
            models.mkdir()
            production = models / "production.joblib"
            production.write_bytes(b"production model sentinel")
            before = production.stat().st_mtime_ns
            with patch("main.PROCESSED_DATA_DIR", root), patch.object(
                pipeline, "MODELS_DIR", models
            ), patch("src.models.race_model.train_and_save_race_model", side_effect=save_artifact):
                artifact = pipeline._train_models(root / "features.parquet", features)
            self.assertEqual(artifact["model"], "temporary")
            self.assertFalse(locations[0].exists())
            self.assertEqual(production.stat().st_mtime_ns, before)
            self.assertEqual(production.read_bytes(), b"production model sentinel")
            self.assertEqual(list(models.iterdir()), [production])

    def test_backtest_training_cutoff(self):
        rows = []
        for year in (2023, 2024, 2025):
            for i in range(4):
                rows.append(dict(Year=year, RoundNumber=1, SessionType="R",
                                 SessionStartUtc=pd.Timestamp(f"{year}-03-01", tz="UTC"),
                                 Abbreviation=f"D{i}", target_position=i + 1,
                                 target_winner=int(i == 0), target_podium=int(i < 3)))
        training, held_out = pipeline._prepare_backtest_data(
            pd.DataFrame(rows), 2024, 1, "Test", "race"
        )
        self.assertTrue((training.SessionStartUtc < held_out.SessionStartUtc.min()).all())
        self.assertEqual(set(training.Year), {2023})

    def test_missing_session_is_retried_without_reloading_saved_sessions(self):
        event = pd.Series(dict(RoundNumber=1, EventName="Test", Session1="Practice 1",
                               Session1DateUtc="2024-01-01", Session2="Race",
                               Session2DateUtc="2024-01-02"))
        with tempfile.TemporaryDirectory() as directory:
            raw = Path(directory)
            pd.DataFrame({"RoundNumber": [1], "Abbreviation": ["AAA"]}).to_parquet(
                raw / "2024_r_results.parquet", index=False)
            with patch("main.RAW_DATA_DIR", raw):
                self.assertEqual(pipeline._missing_sessions(2024, event), {"FP1"})

    def test_race_predictions_table_excludes_actuals_and_metadata(self):
        frame = pd.DataFrame(dict(Abbreviation=["AAA"], DriverName=["A Driver"],
                                  TeamName=["Test"], win_probability=[1.0],
                                  podium_probability=[1.0], expected_position=[1.0],
                                  actual_position=[1], year=[2024]))
        stream = io.StringIO()
        with redirect_stdout(stream):
            pipeline._print_predictions(frame, "race")
        output = stream.getvalue()
        self.assertIn("RACE PREDICTIONS", output)
        self.assertNotIn("actual_position", output)
        self.assertNotIn("year", output)


if __name__ == "__main__":
    unittest.main()
