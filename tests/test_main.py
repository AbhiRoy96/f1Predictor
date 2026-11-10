import tempfile
import unittest
from pathlib import Path
from unittest.mock import call, patch

import joblib
import numpy as np
import pandas as pd

import main as pipeline


class _FixedPredictionModel:
    def predict_proba(self, features, groups=None):
        positive = np.linspace(0.7, 0.3, len(features))
        return np.column_stack((1 - positive, positive))

    def predict(self, features):
        return np.linspace(1.5, 2.5, len(features))


class MainCliTests(unittest.TestCase):
    def test_cli_forwards_session_year_and_round_name(self):
        with patch.object(pipeline, "run_pipeline") as run_pipeline:
            pipeline.main(["race", "2026", "Brazilian Grand Prix"])

        run_pipeline.assert_called_once_with(
            "race", 2026, "Brazilian Grand Prix", measure_variation=False
        )

    def test_cli_forwards_measure_variation_flag(self):
        with patch.object(pipeline, "run_pipeline") as run_pipeline:
            pipeline.main(["race", "2026", "Brazilian Grand Prix", "--measure-variation"])

        run_pipeline.assert_called_once_with(
            "race", 2026, "Brazilian Grand Prix", measure_variation=True
        )

    def test_cli_rejects_year_before_2021(self):
        with self.assertRaises(SystemExit):
            pipeline.main(["qualifying", "2020", "Brazilian Grand Prix"])


class MainPipelineTests(unittest.TestCase):
    def test_collection_starts_in_2021_and_includes_target_year(self):
        with patch("main.collect_event") as collect_event, patch(
                "main.collect_driver_constructor_stats"
        ) as collect_stats:
            pipeline.collect_historical_data(2023, "Brazilian Grand Prix")

        self.assertEqual(
            collect_event.call_args_list,
            [
                call(year, "Brazilian Grand Prix", raise_on_failure=False)
                for year in (2021, 2022, 2023)
            ],
        )
        collect_stats.assert_called_once_with()

    def test_feature_file_uses_session_table_and_only_2021_through_target_year(self):
        source = pd.DataFrame({"Year": [2020, 2021, 2026, 2027]})
        qualifying = pd.DataFrame({"Year": [2021, 2026], "RoundNumber": [1, 2]})

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("main.RAW_DATA_DIR", root / "raw"), patch(
                    "main.PROCESSED_DATA_DIR", root / "processed"
            ), patch("main.collect_historical_data") as collect_data, patch(
                "main.load_results", return_value=source
            ), patch("main.build_features", return_value={"Q": qualifying}) as build_features:
                path = pipeline.ensure_processed_data("qualifying", 2026, "Brazilian Grand Prix")

            collect_data.assert_called_once_with(2026, "Brazilian Grand Prix")
            self.assertEqual(build_features.call_args.args[0]["Year"].tolist(), [2021, 2026])
            self.assertEqual(path.name, "qualifying_features.parquet")
            self.assertTrue(path.exists())

    def test_brazilian_event_alias_resolves_to_saved_sao_paulo_round(self):
        schedule = pd.DataFrame([{
            "EventName": "São Paulo Grand Prix",
            "Location": "São Paulo",
            "Country": "Brazil",
            "RoundNumber": 19,
        }])

        with tempfile.TemporaryDirectory() as directory:
            schedule_path = Path(directory) / "2026_schedule.parquet"
            schedule.to_parquet(schedule_path, index=False)
            with patch("main.RAW_DATA_DIR", Path(directory)):
                resolved = pipeline._resolve_target_event(2026, "Brazilian Grand Prix")

        self.assertEqual(resolved, (19, "São Paulo Grand Prix"))

    def test_missing_requested_event_session_raises_clear_error(self):
        features = pd.DataFrame({"Year": [2026], "RoundNumber": [20], "Abbreviation": ["AAA"]})

        with self.assertRaisesRegex(RuntimeError, "qualifying.*Brazilian Grand Prix.*2026"):
            pipeline._select_target_event(features, 2026, 21, "Brazilian Grand Prix", "qualifying")

    def test_backtest_holdout_excludes_only_requested_event(self):
        features = pd.DataFrame([
            {"Year": 2021, "RoundNumber": 1, "SessionType": "R", "Abbreviation": "AAA",
             "target_winner": 1, "target_podium": 1, "target_position": 1},
            {"Year": 2021, "RoundNumber": 1, "SessionType": "R", "Abbreviation": "BBB",
             "target_winner": 0, "target_podium": 0, "target_position": 2},
            {"Year": 2022, "RoundNumber": 2, "SessionType": "R", "Abbreviation": "AAA",
             "target_winner": 0, "target_podium": 1, "target_position": 2},
            {"Year": 2022, "RoundNumber": 2, "SessionType": "R", "Abbreviation": "BBB",
             "target_winner": 1, "target_podium": 1, "target_position": 1},
        ])

        training, held_out = pipeline._prepare_backtest_data(
            features, 2022, 2, "Test Grand Prix", "race"
        )

        self.assertEqual(set(training["Year"]), {2021})
        self.assertEqual(set(held_out["Year"]), {2022})
        self.assertEqual(set(held_out["RoundNumber"]), {2})

    def test_backtest_rejects_incomplete_target_results(self):
        features = pd.DataFrame([
            {"Year": 2022, "RoundNumber": 2, "SessionType": "R", "Abbreviation": "AAA",
             "target_winner": 1, "target_podium": 1, "target_position": 1},
            {"Year": 2022, "RoundNumber": 2, "SessionType": "R", "Abbreviation": "BBB",
             "target_winner": 0, "target_podium": None, "target_position": None},
        ])

        with self.assertRaisesRegex(ValueError, "completed target results"):
            pipeline._prepare_backtest_data(features, 2022, 2, "Test Grand Prix", "race")

    def test_variation_includes_probability_and_position_errors(self):
        predictions = pd.DataFrame([
            {"Abbreviation": "AAA", "win_probability": 0.8, "podium_probability": 0.9,
             "expected_position": 1.5},
            {"Abbreviation": "BBB", "win_probability": 0.2, "podium_probability": 0.4,
             "expected_position": 2.5},
        ])
        actuals = pd.DataFrame([
            {"Abbreviation": "AAA", "target_winner": 0, "target_podium": 1, "target_position": 2},
            {"Abbreviation": "BBB", "target_winner": 1, "target_podium": 1, "target_position": 1},
        ])

        measured, summary = pipeline._measure_prediction_variation(predictions, actuals)

        self.assertIn("win_probability_absolute_error", measured)
        self.assertIn("podium_probability_absolute_error", measured)
        self.assertIn("position_error", measured)
        self.assertIn("absolute_position_error", measured)
        self.assertAlmostEqual(summary["position_mae"], 1.0)
        self.assertAlmostEqual(summary["win_probability_brier"], 0.64)

    def test_pipeline_persists_prediction_variation_and_simulation_artifacts(self):
        features = pd.DataFrame([
            {"Year": 2021, "RoundNumber": 1, "SessionType": "R", "Abbreviation": "AAA",
             "TeamName": "Team A", "pace": 2.0, "target_winner": 1, "target_podium": 1,
             "target_position": 1},
            {"Year": 2021, "RoundNumber": 1, "SessionType": "R", "Abbreviation": "BBB",
             "TeamName": "Team B", "pace": 1.0, "target_winner": 0, "target_podium": 0,
             "target_position": 2},
            {"Year": 2022, "RoundNumber": 2, "SessionType": "R", "Abbreviation": "AAA",
             "TeamName": "Team A", "pace": 2.0, "target_winner": 0, "target_podium": 1,
             "target_position": 2},
            {"Year": 2022, "RoundNumber": 2, "SessionType": "R", "Abbreviation": "BBB",
             "TeamName": "Team B", "pace": 1.0, "target_winner": 1, "target_podium": 1,
             "target_position": 1},
        ])

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            feature_path = root / "race_features.parquet"
            predictions_dir = root / "predictions"
            predictions_dir.mkdir()
            features.to_parquet(feature_path, index=False)
            winner_path = root / "winner.joblib"
            podium_path = root / "podium.joblib"
            position_path = root / "position.joblib"
            joblib.dump({"model": _FixedPredictionModel(), "feature_columns": ["pace"]}, winner_path)
            joblib.dump({"model": _FixedPredictionModel()}, podium_path)
            joblib.dump({"model": _FixedPredictionModel()}, position_path)
            simulation_results = pd.DataFrame({
                "Abbreviation": ["AAA", "BBB"],
                "TeamName": ["Team A", "Team B"],
                "simulated_win_prob": [0.6, 0.4],
                "simulated_podium_prob": [0.8, 0.7],
                "simulated_dnf_prob": [0.1, 0.2],
                "expected_position": [1.5, 2.5],
            })

            with patch("main.ensure_processed_data", return_value=feature_path), patch(
                "main._resolve_target_event", return_value=(2, "Test Grand Prix")
            ), patch("main.validate_features_df", return_value=(True, [])), patch(
                "main.PREDICTIONS_DATA_DIR", predictions_dir
            ), patch("main._train_models", return_value=(
                {"model_path": winner_path}, {"model_path": podium_path}, {"model_path": position_path}
            )) as train_models, patch(
                "main.MonteCarloRaceSimulator"
            ) as simulator:
                simulator.return_value.simulate.return_value = simulation_results
                pipeline.run_pipeline("race", 2022, "Test Grand Prix", measure_variation=True)

            training_features = train_models.call_args.args[1]
            self.assertEqual(set(training_features["Year"]), {2021})
            prediction_path = predictions_dir / "predictions_race_2022_rnd2.parquet"
            variation_path = predictions_dir / "variation_race_2022_rnd2.parquet"
            simulation_path = predictions_dir / "simulation_race_2022_rnd2.parquet"
            saved_predictions = pd.read_parquet(prediction_path)
            saved_variation = pd.read_parquet(variation_path)
            saved_simulation = pd.read_parquet(simulation_path)

        self.assertEqual(saved_predictions.loc[0, "event_name"], "Test Grand Prix")
        self.assertTrue(saved_predictions["variation_measured"].all())
        self.assertIn("position_error", saved_predictions)
        self.assertIn("position_mae", saved_variation)
        self.assertEqual(saved_simulation.loc[0, "scenario"], "standard_dry")


if __name__ == "__main__":
    unittest.main()
