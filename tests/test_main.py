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
from tests.test_race_model import race_fixture


class _FixedRaceModel:
    def predict(self, frame, **kwargs):
        output = frame[["Abbreviation", "TeamName"]].copy()
        output["win_probability"] = [0.4, 0.3, 0.2, 0.1]
        output["podium_probability"] = [0.9, 0.8, 0.7, 0.6]
        output["top10_probability"] = 1.0
        output["expected_position"] = [1.5, 2.0, 3.0, 3.5]
        return output


def _features():
    return pd.DataFrame([
        dict(Year=year, RoundNumber=1, SessionType="R", Abbreviation=f"D{i}",
             SessionStartUtc=pd.Timestamp(f"{year}-03-01", tz="UTC"), TeamName=f"Team {i // 2}",
             pace=float(i), target_winner=int(i == 0), target_podium=int(i < 3), target_position=i + 1)
        for year in (2023, 2024, 2025) for i in range(4)
    ])


class MainCliTests(unittest.TestCase):
    def test_cli_forwards_session_year_and_round_name(self):
        with patch.object(pipeline, "run_pipeline") as run:
            pipeline.main(["race", "2026", "Brazilian Grand Prix"])
        self.assertEqual(run.call_args.args, ("race", 2026, "Brazilian Grand Prix"))
        self.assertFalse(run.call_args.kwargs["measure_variation"])
        self.assertEqual(run.call_args.kwargs["action"], "auto")
        self.assertEqual(run.call_args.kwargs["stage"], "post_quali")

    def test_cli_forwards_measure_variation_flag(self):
        with patch.object(pipeline, "run_pipeline") as run:
            pipeline.main(["race", "2026", "Brazilian Grand Prix", "--measure-variation"])
        self.assertTrue(run.call_args.kwargs["measure_variation"])

    def test_separate_commands_and_stage(self):
        for action in ("train", "predict", "evaluate"):
            with self.subTest(action=action), patch.object(pipeline, "run_pipeline") as run:
                pipeline.main([action, "race", "2026", "Brazil", "--stage", "post_fp", "--skip-collection"])
            self.assertEqual(run.call_args.kwargs["action"], action)
            self.assertEqual(run.call_args.kwargs["stage"], "post_fp")
            self.assertTrue(run.call_args.kwargs["skip_collection"])

    def test_cli_rejects_year_before_2021(self):
        with self.assertRaises(SystemExit):
            pipeline.main(["qualifying", "2020", "Brazilian Grand Prix"])


class MainPipelineTests(unittest.TestCase):
    def test_evaluation_skips_unobserved_upcoming_target_and_forwards_settings(self):
        frame = race_fixture(10)
        frame.loc[frame.RoundNumber.eq(10), "target_position"] = np.nan
        result = {"metrics": pd.DataFrame({"win_log_loss": [1.0]})}
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(pipeline, "REPORTS_DIR", Path(directory)), patch(
                "src.models.evaluate.run_race_walk_forward", return_value=result
            ) as run:
                pipeline._evaluate(frame, "race", 2026, 10, "post_quali", seed=7, n_simulations=123)
        self.assertEqual(sorted(run.call_args.args[0].RoundNumber.unique()), list(range(1, 10)))
        self.assertEqual(run.call_args.kwargs, {"seed": 7, "n_simulations": 123})

    def test_evaluation_rejects_partially_observed_classification(self):
        frame = race_fixture(10)
        frame.loc[frame.index[-1], "target_position"] = np.nan
        with self.assertRaisesRegex(ValueError, "Partially observed"):
            pipeline._evaluate(frame, "race", 2026, 10, "post_quali")

    def test_collection_starts_in_2021_and_selects_only_related_rounds(self):
        schedule = pd.DataFrame([dict(RoundNumber=i, EventName=f"Round {i}") for i in (1, 2, 3)])
        with tempfile.TemporaryDirectory() as directory:
            with patch("main.RAW_DATA_DIR", Path(directory)), patch(
                "main.fastf1.get_event_schedule", return_value=schedule
            ), patch.object(pipeline, "_missing_sessions") as missing:
                # Each event initially lacks R; the post-collection check confirms it was saved.
                missing.side_effect = [{"R"}, set()] * 8
                with patch("main.collect_event") as collect, patch(
                    "main.collect_driver_constructor_stats"
                ) as stats:
                    pipeline.collect_historical_data(2023, "Round 2")
        self.assertEqual([(c.args[0], c.args[1]) for c in collect.call_args_list],
                         [(year, f"Round {round_number}") for year in (2021, 2022, 2023)
                          for round_number in (1, 2)])
        self.assertTrue(all(c.kwargs["raise_on_failure"] for c in collect.call_args_list))
        self.assertTrue(all(c.kwargs["session_types"] == {"R"} for c in collect.call_args_list))
        stats.assert_called_once_with()

    def test_collection_does_not_hide_missing_sessions(self):
        schedule = pd.DataFrame([dict(RoundNumber=1, EventName="Test")])
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(pipeline, "HISTORICAL_START_YEAR", 2023), patch(
                "main.RAW_DATA_DIR", Path(directory)
            ), patch("main.fastf1.get_event_schedule", return_value=schedule), patch.object(
                pipeline, "_missing_sessions", return_value={"R"}
            ), patch("main.collect_event"), patch("main.collect_driver_constructor_stats"):
                with self.assertRaisesRegex(RuntimeError, "Historical collection incomplete"):
                    pipeline.collect_historical_data(2023, "Test")

    def test_feature_file_uses_session_table_and_only_2021_through_target_year(self):
        source = pd.DataFrame({"Year": [2020, 2021, 2026, 2027]})
        qualifying = pd.DataFrame({"Year": [2021, 2026], "RoundNumber": [1, 2]})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("main.RAW_DATA_DIR", root / "raw"), patch(
                "main.PROCESSED_DATA_DIR", root / "processed"
            ), patch.object(pipeline, "collect_historical_data") as collect, patch(
                "main.load_results", return_value=source
            ), patch("main.build_features", return_value={"Q": qualifying}) as build:
                path = pipeline.ensure_processed_data("qualifying", 2026, "Brazilian Grand Prix")
                cached = pipeline.ensure_processed_data("qualifying", 2026, "Brazilian Grand Prix")
            self.assertEqual(collect.call_count, 2)
            self.assertEqual(build.call_count, 1)
            self.assertEqual(build.call_args.args[0]["Year"].tolist(), [2021, 2026])
            self.assertEqual(build.call_args.kwargs["stage"], "post_quali")
            self.assertEqual(set(build.call_args.kwargs), {"laps", "weather", "messages", "news", "stage"})
            self.assertEqual(path, cached)
            self.assertTrue(path.exists())

    def test_brazilian_event_alias_resolves_to_saved_sao_paulo_round(self):
        schedule = pd.DataFrame([dict(EventName="São Paulo Grand Prix", Location="São Paulo",
                                      Country="Brazil", RoundNumber=19)])
        with tempfile.TemporaryDirectory() as directory:
            schedule.to_parquet(Path(directory) / "2026_schedule.parquet", index=False)
            with patch("main.RAW_DATA_DIR", Path(directory)):
                resolved = pipeline._resolve_target_event(2026, "Brazilian Grand Prix")
        self.assertEqual(resolved, (19, "São Paulo Grand Prix"))

    def test_missing_requested_event_session_raises_clear_error(self):
        features = pd.DataFrame({"Year": [2026], "RoundNumber": [20], "Abbreviation": ["AAA"]})
        with self.assertRaisesRegex(RuntimeError, "qualifying.*Brazilian Grand Prix.*2026"):
            pipeline._select_target_event(features, 2026, 21, "Brazilian Grand Prix", "qualifying")

    def test_backtest_holdout_excludes_requested_and_all_later_events(self):
        training, held_out = pipeline._prepare_backtest_data(_features(), 2024, 1, "Test", "race")
        self.assertEqual(set(training.Year), {2023})
        self.assertEqual(set(held_out.Year), {2024})
        self.assertTrue((training.SessionStartUtc < held_out.SessionStartUtc.min()).all())

    def test_backtest_rejects_incomplete_target_results(self):
        features = _features()
        features.loc[features.Year.eq(2024), "target_position"] = np.nan
        with self.assertRaisesRegex(ValueError, "completed target results"):
            pipeline._prepare_backtest_data(features, 2024, 1, "Test", "race")

    def test_variation_includes_probability_and_position_errors(self):
        predictions = pd.DataFrame(dict(Abbreviation=["AAA", "BBB"], win_probability=[0.8, 0.2],
                                         podium_probability=[0.9, 0.4], expected_position=[1.5, 2.5]))
        actuals = pd.DataFrame(dict(Abbreviation=["AAA", "BBB"], target_winner=[0, 1],
                                    target_podium=[1, 1], target_position=[2, 1]))
        measured, summary = pipeline._measure_prediction_variation(predictions, actuals)
        self.assertIn("win_probability_absolute_error", measured)
        self.assertIn("podium_probability_absolute_error", measured)
        self.assertIn("position_error", measured)
        self.assertIn("absolute_position_error", measured)
        self.assertAlmostEqual(summary["position_mae"], 1.0)
        self.assertAlmostEqual(summary["win_probability_brier"], 0.64)

    def test_pipeline_persists_variation_without_touching_production_models(self):
        features = _features()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            feature_path = root / "race_features.parquet"
            features.to_parquet(feature_path, index=False)
            models = root / "models"
            models.mkdir()
            sentinel = models / "production.joblib"
            joblib.dump({"production": True}, sentinel)
            before = (sentinel.stat().st_mtime_ns, sentinel.read_bytes())
            with patch.object(pipeline, "ensure_processed_data", return_value=feature_path), patch.object(
                pipeline, "_resolve_target_event", return_value=(1, "Test Grand Prix")
            ), patch("main.validate_features_df", return_value=(True, [])), patch(
                "main.PREDICTIONS_DATA_DIR", root / "predictions"
            ), patch("main.PROCESSED_DATA_DIR", root), patch.object(
                pipeline, "MODELS_DIR", models
            ), patch.object(pipeline, "_train_models", return_value={"model": _FixedRaceModel()}) as train:
                with redirect_stdout(io.StringIO()) as output:
                    pipeline.run_pipeline("race", 2024, "Test Grand Prix", measure_variation=True)
            self.assertEqual(set(train.call_args.args[1].Year), {2023})
            saved = pd.read_parquet(root / "predictions/predictions_race_2024_rnd1.parquet")
            variation = pd.read_parquet(root / "predictions/variation_race_2024_rnd1.parquet")
            self.assertEqual(saved.loc[0, "event_name"], "Test Grand Prix")
            self.assertTrue(saved.variation_measured.all())
            self.assertIn("position_error", saved)
            self.assertIn("position_mae", variation)
            self.assertEqual(saved.stage.unique().tolist(), ["post_quali"])
            self.assertEqual(saved.training_races.unique().tolist(), [1])
            self.assertAlmostEqual(saved.win_probability.sum(), 1)
            self.assertAlmostEqual(saved.podium_probability.sum(), 3)
            self.assertIn("RACE PREDICTIONS", output.getvalue())
            self.assertEqual(before, (sentinel.stat().st_mtime_ns, sentinel.read_bytes()))
            self.assertEqual(list(models.iterdir()), [sentinel])


if __name__ == "__main__":
    unittest.main()
