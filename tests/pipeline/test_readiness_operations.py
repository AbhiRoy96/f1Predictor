"""Scheduled admission and honest live evidence, with no external requests."""

import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import pandas as pd

import main as pipeline
from scripts.live_scoreboard import running_table, score_forecasts
from scripts.scheduled_forecast import enqueue, forecast_payload


class ReadinessOperationsTests(unittest.TestCase):
    def test_live_archive_is_immutable_and_excludes_backtests_or_late_forecasts(self):
        forecasts, actuals = self.forecasts_and_results()
        metadata = {"prediction_generated_at_utc": "2026-03-01T14:00:00Z",
                    "training_end_utc": "2026-02-20T14:00:00Z", "variation_measured": False}
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            with patch.object(pipeline, "PREDICTIONS_DATA_DIR", Path(directory)):
                path = pipeline._archive_live_forecast(forecasts, actuals, metadata, "race_2026_rnd1", "post_quali")
                self.assertEqual(len(pd.read_parquet(path)), 3)
                with self.assertRaises(FileExistsError):
                    pipeline._archive_live_forecast(forecasts, actuals, metadata, "race_2026_rnd1", "post_quali")
                self.assertIsNone(pipeline._archive_live_forecast(
                    forecasts, actuals, metadata | {"variation_measured": True}, "backtest", "post_quali"))
                self.assertIsNone(pipeline._archive_live_forecast(
                    forecasts, actuals, metadata | {"prediction_generated_at_utc": "2026-03-03T00:00:00Z"},
                    "late", "post_quali"))
                self.assertEqual(len(list((Path(directory) / "archive").glob("*.parquet"))), 1)

    def test_main_accepts_scheduled_phase_kwargs_and_rejects_bad_stage_before_collection(self):
        with patch.object(pipeline, "run_pipeline") as run:
            pipeline.main(["predict", "race", "2026", "Test", "--forecast-phase", "official_grid",
                           "--source-published-at-utc", "2026-03-01T10:00:00Z"])
        self.assertEqual(run.call_args.kwargs["forecast_phase"], "official_grid")
        self.assertEqual(run.call_args.kwargs["source_published_at_utc"], "2026-03-01T10:00:00Z")
        with patch.object(pipeline, "ensure_processed_data") as collect:
            with self.assertRaisesRegex(ValueError, "stage=post_quali"):
                pipeline.run_pipeline("race", 2026, "Test", stage="pre_weekend",
                                      forecast_phase="official_grid", source_published_at_utc="2026-03-01T10:00:00Z")
        collect.assert_not_called()

    def test_recent_seasons_collect_all_results_older_seasons_remain_bounded(self):
        schedule = pd.DataFrame([{"EventName": f"Event {i}", "RoundNumber": i,
                                  "Location": "Test", "Country": "Test"} for i in range(1, 4)])
        saved = set()
        calls = []

        def missing(year, event):
            return set() if (year, event.RoundNumber) in saved else {"R"}

        def collect(year, name, **options):
            calls.append((year, name, options))
            saved.add((year, options["event"].RoundNumber))

        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            with patch.object(pipeline, "RAW_DATA_DIR", Path(directory)), patch.object(
                    pipeline.fastf1, "get_event_schedule", return_value=schedule), patch.object(
                    pipeline, "_resolve_event", return_value=schedule.iloc[0]), patch.object(
                    pipeline, "select_related_events", side_effect=lambda *a: schedule.iloc[:1]), patch.object(
                    pipeline, "_missing_sessions", side_effect=missing), patch.object(
                    pipeline, "collect_event", side_effect=collect), patch.object(
                    pipeline, "collect_driver_constructor_stats"):
                pipeline.collect_historical_data(2023, "Event 1", events_per_season=1)
        self.assertEqual(len([c for c in calls if c[0] == 2021]), 1)
        for year in (2022, 2023):
            recent = [c for c in calls if c[0] == year]
            self.assertEqual(len(recent), 3)
            self.assertTrue(all(c[2]["results_only"] for c in recent))

    def test_scheduled_phases_have_explicit_confirmed_source_and_time_guards(self):
        options = dict(year=2026, event="Test", qualifying_completed_at="2026-03-01T10:00:00Z",
                       race_start="2026-03-02T10:00:00Z", now="2026-03-01T15:00:00Z")
        payload = forecast_payload(phase="post_quali", **options)
        self.assertEqual(payload["stage"], "post_quali")
        self.assertEqual(payload["forecast_phase"], "post_quali")
        with self.assertRaisesRegex(ValueError, "publication"):
            forecast_payload(phase="official_grid", **options)
        grid = forecast_payload(phase="official_grid", grid_published_at="2026-03-01T14:00:00Z", **options)
        self.assertEqual(grid["forecast_phase"], "official_grid")
        with self.assertRaisesRegex(ValueError, "before the race"):
            forecast_payload(phase="post_quali", **(options | {"now": "2026-03-03T00:00:00Z"}))
        with self.assertRaisesRegex(ValueError, "timezone"):
            forecast_payload(phase="post_quali", **(options | {"race_start": "2026-03-02"}))

    def test_enqueue_calls_authenticated_admission_once_and_never_writes(self):
        response = io.BytesIO(b'{"id":"job","status":"queued"}')
        response.status = 202
        opener = Mock()
        opener.open.return_value = response
        with patch("builtins.open", side_effect=AssertionError("No disk writes")), patch.object(
                Path, "mkdir", side_effect=AssertionError("No directory writes")):
            result = enqueue("https://api.example.test", "x" * 32, {"stage": "post_quali"}, opener=opener)
        self.assertEqual(result["id"], "job")
        opener.open.assert_called_once()
        request = opener.open.call_args.args[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.get_header("X-gridcast-proxy-token"), "x" * 32)
        self.assertEqual(request.get_header("X-gridcast-user"), request.get_header("X-gridcast-client-ip"))
        with self.assertRaises(ValueError):
            enqueue("https://user:password@api.example.test", "x" * 32, {})
        with self.assertRaises(ValueError):
            enqueue("http://public.example.test", "x" * 32, {})

    @staticmethod
    def forecasts_and_results():
        forecasts = pd.DataFrame({"Year": 2026, "RoundNumber": 1, "SessionType": "R",
                                  "Abbreviation": ["AAA", "BBB", "CCC"], "stage": "post_quali",
                                  "model_version": "test-version", "win_probability": [0.6, 0.3, 0.1],
                                  "podium_probability": 1., "top10_probability": 1.,
                                  "expected_position": [1.5, 2., 2.5],
                                  "prediction_generated_at_utc": "2026-03-01T14:00:00Z",
                                  "training_end_utc": "2026-02-20T14:00:00Z", "variation_measured": False})
        actuals = forecasts[["Year", "RoundNumber", "SessionType", "Abbreviation"]].copy()
        actuals["Position"] = [1, 2, 3]
        actuals["SessionStartUtc"] = "2026-03-02T14:00:00Z"
        return forecasts, actuals

    def test_scoreboard_scores_only_pre_race_forecasts_and_running_count(self):
        forecasts, actuals = self.forecasts_and_results()
        scores = score_forecasts(forecasts, actuals)
        self.assertEqual(len(scores), 1)
        self.assertAlmostEqual(scores.position_mae.iloc[0], 1 / 3)
        self.assertEqual(running_table(scores).n_races.iloc[0], 1)
        # Repeated input files must not silently double the field or the evidence.
        with self.assertRaisesRegex(ValueError, "fields"):
            score_forecasts(pd.concat([forecasts, forecasts]), actuals)
        forecasts["prediction_generated_at_utc"] = "2026-03-03T00:00:00Z"
        with self.assertRaisesRegex(ValueError, "Late forecasts"):
            score_forecasts(forecasts, actuals)

    def test_scoreboard_rejects_backtests_and_incomplete_classification(self):
        forecasts, actuals = self.forecasts_and_results()
        forecasts["variation_measured"] = True
        with self.assertRaisesRegex(ValueError, "Backtests"):
            score_forecasts(forecasts, actuals)
        forecasts["variation_measured"] = False
        actuals.loc[0, "Position"] = None
        with self.assertRaisesRegex(ValueError, "complete"):
            score_forecasts(forecasts, actuals)


if __name__ == "__main__":
    unittest.main()
