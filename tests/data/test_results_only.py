"""Results-only collection and elapsed-day form windows without live downloads."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pandas as pd
import requests

from src.data import collect_season
from src.features.engineer_features import build_features
from tests.data.test_data_pipeline import _result


class ResultsOnlyTests(unittest.TestCase):
    def test_results_only_retries_and_respects_retry_after(self):
        response = requests.Response()
        response.status_code = 429
        response.headers["Retry-After"] = "4"
        session = SimpleNamespace(load=Mock(side_effect=[
            requests.HTTPError("rate limited", response=response), None]))
        with patch.object(collect_season.time, "sleep") as sleep:
            collect_season._load_session(session, True)
        self.assertEqual(session.load.call_count, 2)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [4])
        session.load = Mock(side_effect=requests.Timeout("upstream timeout"))
        with patch.object(collect_season.time, "sleep"), self.assertRaises(requests.Timeout):
            collect_season._load_session(session, True)
        self.assertEqual(session.load.call_count, 3)

    def test_results_only_never_loads_or_persists_laps(self):
        event = {"EventName": "Test", "RoundNumber": 1,
                 "Session1": "Practice 1", "Session1DateUtc": "2026-01-01T10:00:00Z",
                 "Session2": "Qualifying", "Session2DateUtc": "2026-01-02T10:00:00Z",
                 "Session3": "Race", "Session3DateUtc": "2026-01-03T10:00:00Z"}
        session = SimpleNamespace(results=pd.DataFrame({"Abbreviation": ["AAA"], "Position": [1]}),
                                  load=Mock())
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            with patch.object(collect_season, "OUTPUT", Path(directory)), patch.object(
                    collect_season, "_CACHE_ENABLED", True), patch.object(
                    collect_season.fastf1, "get_session", return_value=session), patch.object(
                    collect_season.time, "sleep"):
                kinds = collect_season.collect_event(2026, "Test", event=event,
                    results_only=True, now="2026-01-02T15:00:00Z", raise_on_failure=True)
            self.assertEqual(kinds, ["Q"])
            session.load.assert_called_once_with(laps=False, telemetry=False, weather=False, messages=False)
            self.assertFalse(list(Path(directory).glob("*_laps.parquet")))
            self.assertFalse(list(Path(directory).glob("*_weather.parquet")))

    def test_form_windows_expire_at_target_cutoff_and_ignore_future(self):
        rows = [
            _result(2025, "R", "2025-03-01", "AAA", "Team", 1, 25),
            {**_result(2025, "R", "2025-12-01", "AAA", "Team", 5, 10), "RoundNumber": 2},
            _result(2026, "R", "2026-03-02", "AAA", "Team", 3, 15),
            {**_result(2026, "R", "2026-03-10", "AAA", "Team", 1, 25), "RoundNumber": 2},
        ]
        result = build_features(pd.DataFrame(rows), stage="pre_weekend")["R"]
        row = result.query("Year == 2026 and RoundNumber == 1").iloc[0]
        # New regulation era has no prior race: never borrow the target/future outcome.
        self.assertEqual(row.driver_race_form_90d_is_missing, 1)
        row = result.query("Year == 2025 and RoundNumber == 2").iloc[0]
        self.assertEqual(row.driver_race_form_90d_is_missing, 1)
        self.assertEqual(row.driver_race_form_180d_is_missing, 1)
        self.assertEqual(row.driver_race_points_180d, 0)


if __name__ == "__main__":
    unittest.main()
