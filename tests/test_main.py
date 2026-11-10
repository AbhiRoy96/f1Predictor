import tempfile
import unittest
from pathlib import Path
from unittest.mock import call, patch

import pandas as pd

import main as pipeline


class MainCliTests(unittest.TestCase):
    def test_cli_forwards_session_year_and_round_name(self):
        with patch.object(pipeline, "run_pipeline") as run_pipeline:
            pipeline.main(["race", "2026", "Brazilian Grand Prix"])

        run_pipeline.assert_called_once_with("race", 2026, "Brazilian Grand Prix")

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


if __name__ == "__main__":
    unittest.main()
