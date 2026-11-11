"""Bounded historical selection, alias handling and incremental download contracts."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

import main as pipeline
from src.data.event_selection import select_related_events, similar_circuit_keys


def schedule_fixture():
    locations = ["Bahrain", "Austin", "Monza", "Silverstone", "Monaco", "Baku",
                 "Singapore", "São Paulo", "Jeddah", "Suzuka", "Abu Dhabi", "Melbourne"]
    return pd.DataFrame([
        dict(RoundNumber=i, EventName=f"Event {i}", Location=location)
        for i, location in enumerate(locations, 1)
    ])


class EventSelectionTests(unittest.TestCase):
    def setUp(self):
        self.schedule = schedule_fixture()
        self.target = self.schedule.iloc[7]

    def test_current_season_cap_includes_target_predecessors_and_similar_circuits(self):
        selected = select_related_events(self.schedule, self.target, 2026, 2026)
        self.assertEqual(selected.RoundNumber.tolist(), [2, 4, 5, 6, 7, 8])
        self.assertTrue(selected.RoundNumber.le(8).all())
        for cap in (1, 2, 3, 6, 20):
            selected = select_related_events(self.schedule, self.target, 2026, 2026, cap)
            self.assertLessEqual(len(selected), cap)
            self.assertIn(8, selected.RoundNumber.tolist())
            self.assertTrue(selected.RoundNumber.is_unique)

    def test_older_seasons_can_include_related_circuits_later_in_the_calendar(self):
        selected = select_related_events(self.schedule, self.target, 2025, 2026)
        self.assertEqual(selected.RoundNumber.tolist(), [4, 5, 6, 7, 8, 10])

    def test_explicit_similarity_override_disable_and_unknown_presets(self):
        override = select_related_events(self.schedule, self.target, 2026, 2026, similar_circuits=["Monza"])
        self.assertIn(3, override.RoundNumber.tolist())
        disabled = select_related_events(self.schedule, self.target, 2026, 2026, similar_circuits=[])
        self.assertEqual(disabled.RoundNumber.tolist(), [3, 4, 5, 6, 7, 8])
        unknown = self.target.copy()
        unknown["Location"] = "Atlantis"
        self.assertEqual(similar_circuit_keys(unknown), [])
        self.assertTrue(similar_circuit_keys(self.target))

    def test_invalid_budget_and_future_season_are_rejected(self):
        for cap in (0, -1, True, 2.5):
            with self.subTest(cap=cap), self.assertRaises(ValueError):
                select_related_events(self.schedule, self.target, 2026, 2026, cap)
        with self.assertRaises(ValueError):
            select_related_events(self.schedule, self.target, 2027, 2026)

    def test_collector_never_downloads_outside_plan_and_reuses_saved_events(self):
        downloaded = set()

        def missing(year, event):
            return set() if (year, int(event.RoundNumber)) in downloaded else {"R"}

        def collect(year, name, **kwargs):
            downloaded.add((year, int(kwargs["event"].RoundNumber)))

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(pipeline, "HISTORICAL_START_YEAR", 2025), patch.object(
                pipeline, "RAW_DATA_DIR", Path(directory)
            ), patch("main.fastf1.get_event_schedule", return_value=self.schedule), patch.object(
                pipeline, "_missing_sessions", side_effect=missing
            ), patch.object(pipeline, "collect_event", side_effect=collect) as collector, patch(
                "main.collect_driver_constructor_stats"
            ):
                pipeline.collect_historical_data(2026, "Event 8")
                self.assertEqual(collector.call_count, 12)
                pipeline.collect_historical_data(2026, "Event 8")
                self.assertEqual(collector.call_count, 12)
        for year in (2025, 2026):
            expected = select_related_events(self.schedule, self.target, year, 2026)
            self.assertEqual({rnd for y, rnd in downloaded if y == year}, set(expected.RoundNumber))

    def test_cli_passes_budget_and_explicit_similarity_override(self):
        with patch.object(pipeline, "run_pipeline") as run:
            pipeline.main(["race", "2026", "Brazil", "--events-per-season", "6",
                           "--similar-circuits", "Austin", "Silverstone"])
        self.assertEqual(run.call_args.kwargs["events_per_season"], 6)
        self.assertEqual(run.call_args.kwargs["similar_circuits"], ["Austin", "Silverstone"])


if __name__ == "__main__":
    unittest.main()
