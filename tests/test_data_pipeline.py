import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from src.data import collect_season
from src.data import collect_driver_constructor_stats
from src.features.engineer_features import build_features


def _result(year, session_type, start, abbreviation, team, position, points, circuit="Bahrain International Circuit"):
    return {
        "Year": year,
        "RoundNumber": 1,
        "EventName": "Bahrain Grand Prix",
        "CircuitName": circuit,
        "SessionType": session_type,
        "SessionStartUtc": pd.Timestamp(start, tz="UTC"),
        "Abbreviation": abbreviation,
        "DriverNumber": "1" if abbreviation == "AAA" else "2",
        "TeamName": team,
        "Position": position,
        "Points": points,
    }


class FeatureEngineeringTests(unittest.TestCase):
    def setUp(self):
        self.results = pd.DataFrame([
            _result(2025, "Q", "2025-03-01 10:00", "AAA", "Team A", 1, 0),
            _result(2025, "Q", "2025-03-01 10:00", "BBB", "Team B", 2, 0),
            _result(2025, "R", "2025-03-02 10:00", "AAA", "Team A", 1, 25),
            _result(2025, "R", "2025-03-02 10:00", "BBB", "Team B", 2, 18),
            _result(2026, "FP1", "2026-03-01 08:00", "AAA", "Team A", 1, 0),
            _result(2026, "FP1", "2026-03-01 08:00", "BBB", "Team B", 2, 0),
            _result(2026, "SQ", "2026-03-01 09:00", "AAA", "Team A", 1, 0),
            _result(2026, "SQ", "2026-03-01 09:00", "BBB", "Team B", 2, 0),
            _result(2026, "Q", "2026-03-01 10:00", "AAA", "Team A", 2, 0),
            _result(2026, "Q", "2026-03-01 10:00", "BBB", "Team B", 1, 0),
            _result(2026, "S", "2026-03-01 14:00", "AAA", "Team A", 1, 8),
            _result(2026, "S", "2026-03-01 14:00", "BBB", "Team B", 2, 7),
            _result(2026, "R", "2026-03-02 10:00", "AAA", "Team A", 2, 18),
            _result(2026, "R", "2026-03-02 10:00", "BBB", "Team B", 1, 25),
        ])
        self.laps = pd.DataFrame([
            {"Year": 2026, "RoundNumber": 1, "SessionType": "FP1", "SessionStartUtc": pd.Timestamp("2026-03-01 08:00", tz="UTC"), "Driver": "AAA", "LapTime": pd.Timedelta(seconds=90), "TyreLife": 3, "Compound": "SOFT"},
            {"Year": 2026, "RoundNumber": 1, "SessionType": "Q", "SessionStartUtc": pd.Timestamp("2026-03-01 10:00", tz="UTC"), "Driver": "AAA", "LapTime": pd.Timedelta(seconds=1), "TyreLife": 99, "Compound": "HARD"},
        ])
        self.weather = pd.DataFrame([
            {"Year": 2026, "RoundNumber": 1, "SessionType": "FP1", "SessionStartUtc": pd.Timestamp("2026-03-01 08:00", tz="UTC"), "AirTemp": 25, "TrackTemp": 35},
            {"Year": 2026, "RoundNumber": 1, "SessionType": "Q", "SessionStartUtc": pd.Timestamp("2026-03-01 10:00", tz="UTC"), "AirTemp": 80, "TrackTemp": 99},
        ])
        self.messages = pd.DataFrame([
            {"Year": 2026, "RoundNumber": 1, "SessionType": "FP1", "SessionStartUtc": pd.Timestamp("2026-03-01 08:00", tz="UTC"), "Message": "Yellow flag"},
            {"Year": 2026, "RoundNumber": 1, "SessionType": "Q", "SessionStartUtc": pd.Timestamp("2026-03-01 10:00", tz="UTC"), "Message": "Virtual Safety Car"},
        ])
        self.news = pd.DataFrame([
            {"Year": 2026, "EventName": "Bahrain Grand Prix", "PublishedAt": "2026-03-01T09:00:00Z", "Text": "Rain could affect tyre choice"},
            {"Year": 2026, "EventName": "Bahrain Grand Prix", "PublishedAt": "2026-03-01T11:00:00Z", "Text": "New tyre update"},
        ])

    def test_features_use_only_information_before_target_session(self):
        tables = build_features(self.results, self.laps, self.weather, self.messages, self.news)
        sprint_qualifying = tables["SQ"].query("Year == 2026 and Abbreviation == 'AAA'").iloc[0]
        qualifying = tables["Q"].query("Year == 2026 and Abbreviation == 'AAA'").iloc[0]
        sprint = tables["S"].query("Year == 2026 and Abbreviation == 'AAA'").iloc[0]
        race = tables["R"].query("Year == 2026 and Abbreviation == 'AAA'").iloc[0]

        self.assertEqual(sprint_qualifying["driver_qualifying_season_starts"], 0)
        self.assertEqual(sprint_qualifying["driver_qualifying_circuit_starts"], 0)
        self.assertEqual(sprint_qualifying["target_pole"], 1)
        self.assertEqual(qualifying["driver_race_circuit_starts"], 1)
        self.assertEqual(qualifying["driver_race_season_starts"], 0)
        self.assertEqual(qualifying["driver_qualifying_circuit_starts"], 1)
        self.assertEqual(qualifying["driver_qualifying_season_starts"], 0)
        self.assertEqual(qualifying["prior_weekend_lap_count"], 1)
        self.assertEqual(qualifying["prior_weekend_best_lap_seconds"], 90)
        self.assertEqual(qualifying["prior_weekend_tracktemp_mean"], 35)
        self.assertEqual(qualifying["prior_weekend_message_count"], 1)
        self.assertEqual(qualifying["prior_news_item_count"], 1)
        self.assertEqual(sprint["driver_qualifying_season_starts"], 1)
        self.assertEqual(sprint["prior_weekend_message_count"], 2)
        self.assertEqual(race["driver_race_season_starts"], 0)
        self.assertEqual(race["driver_race_season_wins"], 0)
        self.assertEqual(race["constructor_race_season_sessions"], 0)
        self.assertEqual(race["target_position"], 2)
        self.assertEqual(race["target_winner"], 0)
        self.assertNotIn("Position", tables["R"].columns)
        self.assertNotIn("Points", tables["R"].columns)

    def test_unavailable_weather_and_news_remain_missing(self):
        tables = build_features(self.results, news=None)
        qualifying = tables["Q"].query("Year == 2026 and Abbreviation == 'AAA'").iloc[0]
        self.assertTrue(pd.isna(qualifying["prior_weekend_tracktemp_mean"]))
        self.assertTrue(pd.isna(qualifying["prior_news_item_count"]))


class CollectionTests(unittest.TestCase):
    def test_event_alias_resolves_by_country_and_loads_by_round_number(self):
        event = {
            "EventName": "São Paulo Grand Prix",
            "Location": "São Paulo",
            "Country": "Brazil",
            "RoundNumber": 19,
            "Session1": "Qualifying",
            "Session1DateUtc": "2021-11-13T19:00:00Z",
        }
        session = SimpleNamespace(
            results=pd.DataFrame([{"Abbreviation": "AAA"}]),
            laps=pd.DataFrame(),
            weather_data=pd.DataFrame(),
            race_control_messages=pd.DataFrame(),
            load=lambda **kwargs: None,
        )

        for year in (2021, 2026):
            with self.subTest(year=year), tempfile.TemporaryDirectory() as directory:
                schedule = pd.DataFrame([event])
                with patch.object(collect_season, "OUTPUT", Path(directory)), patch.object(
                    collect_season, "_CACHE_ENABLED", True
                ), patch.object(
                    collect_season.fastf1, "get_event_schedule", return_value=schedule
                ), patch.object(
                    collect_season.fastf1, "get_session", return_value=session
                ) as get_session:
                    collected = collect_season.collect_event(
                        year,
                        "Brazilian Grand Prix",
                        now="2027-01-01T00:00:00Z",
                    )

                self.assertEqual(collected, ["Q"])
                get_session.assert_called_once_with(year, 19, "Q")
                saved = pd.read_parquet(Path(directory) / f"{year}_q_results.parquet")
                self.assertEqual(saved.loc[0, "EventName"], "São Paulo Grand Prix")

    def test_event_lookup_reports_unknown_and_ambiguous_names(self):
        schedules = {
            2025: pd.DataFrame([{
                "EventName": "Brazilian Grand Prix",
                "Location": "São Paulo",
                "Country": "Brazil",
                "RoundNumber": 1,
            }]),
            2026: pd.DataFrame([
                {"EventName": "Miami Grand Prix", "Location": "Miami", "Country": "United States", "RoundNumber": 1},
                {"EventName": "United States Grand Prix", "Location": "Austin", "Country": "United States", "RoundNumber": 2},
            ]),
        }
        with patch.object(collect_season, "_CACHE_ENABLED", True), patch.object(
            collect_season.fastf1,
            "get_event_schedule",
            side_effect=lambda year, include_testing: schedules[year],
        ):
            with self.assertRaisesRegex(ValueError, "not found"):
                collect_season.collect_event(2025, "Atlantis Grand Prix")
            with self.assertRaisesRegex(ValueError, "ambiguous"):
                collect_season.collect_event(2026, "United States")

    def test_cache_uses_configure_and_falls_back_to_enable_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            configure = SimpleNamespace(
                configure=unittest.mock.Mock(), enable_cache=unittest.mock.Mock()
            )
            with patch.object(collect_season, "CACHE", Path(directory)), patch.object(
                collect_season, "_CACHE_ENABLED", False
            ), patch.object(collect_season.fastf1, "Cache", configure):
                collect_season._enable_cache()
            configure.configure.assert_called_once_with(cache_dir=directory)
            configure.enable_cache.assert_not_called()

            legacy = SimpleNamespace(enable_cache=unittest.mock.Mock())
            with patch.object(collect_season, "CACHE", Path(directory)), patch.object(
                collect_season, "_CACHE_ENABLED", False
            ), patch.object(collect_season.fastf1, "Cache", legacy):
                collect_season._enable_cache()
            legacy.enable_cache.assert_called_once_with(directory)

    def test_cli_defaults_to_configured_seasons(self):
        with patch.object(collect_season, "collect_season") as collect_year, patch.object(
            collect_season, "collect_event"
        ) as collect_event, patch.object(
            collect_season.collect_driver_constructor_stats, "collect_driver_constructor_stats"
        ) as collect_stats:
            collect_season.main([])

        self.assertEqual(
            [call.args[0] for call in collect_year.call_args_list],
            list(collect_season.DEFAULT_SEASONS),
        )
        collect_event.assert_not_called()
        collect_stats.assert_called_once_with()

    def test_cli_collects_explicit_year_including_2020(self):
        with patch.object(collect_season, "collect_season") as collect_year, patch.object(
            collect_season.collect_driver_constructor_stats, "collect_driver_constructor_stats"
        ) as collect_stats:
            collect_season.main(["--year", "2020"])

        collect_year.assert_called_once_with(2020)
        collect_stats.assert_called_once_with()

    def test_cli_collects_event_for_each_explicit_year(self):
        with patch.object(collect_season, "collect_event") as collect_event, patch.object(
            collect_season, "collect_season"
        ) as collect_year, patch.object(
            collect_season.collect_driver_constructor_stats, "collect_driver_constructor_stats"
        ) as collect_stats:
            collect_season.main([
                "--year", "2025", "--year", "2026", "--event", "Brazilian Grand Prix"
            ])

        self.assertEqual(
            collect_event.call_args_list,
            [
                unittest.mock.call(2025, "Brazilian Grand Prix", raise_on_failure=True),
                unittest.mock.call(2026, "Brazilian Grand Prix", raise_on_failure=True),
            ],
        )
        collect_year.assert_not_called()
        collect_stats.assert_called_once_with()

    def test_cli_collects_inclusive_year_range(self):
        with patch.object(collect_season, "collect_season") as collect_year, patch.object(
            collect_season.collect_driver_constructor_stats, "collect_driver_constructor_stats"
        ) as collect_stats:
            status = collect_season.main(["--start-year", "2019", "--end-year", "2021"])

        self.assertEqual(status, 0)
        self.assertEqual([call.args[0] for call in collect_year.call_args_list], [2019, 2020, 2021])
        collect_stats.assert_called_once_with()

    def test_cli_collects_event_for_each_year_in_range(self):
        with patch.object(collect_season, "collect_event") as collect_event, patch.object(
            collect_season, "collect_season"
        ) as collect_year, patch.object(
            collect_season.collect_driver_constructor_stats, "collect_driver_constructor_stats"
        ) as collect_stats:
            status = collect_season.main([
                "--start-year", "2025", "--end-year", "2026", "--event", "Brazilian Grand Prix"
            ])

        self.assertEqual(status, 0)
        self.assertEqual(
            collect_event.call_args_list,
            [
                unittest.mock.call(2025, "Brazilian Grand Prix", raise_on_failure=True),
                unittest.mock.call(2026, "Brazilian Grand Prix", raise_on_failure=True),
            ],
        )
        collect_year.assert_not_called()
        collect_stats.assert_called_once_with()

    def test_cli_rejects_incomplete_or_conflicting_year_selection(self):
        for arguments in (
            ["--start-year", "2025"],
            ["--end-year", "2026"],
            ["--start-year", "2026", "--end-year", "2025"],
            ["--year", "2025", "--start-year", "2025", "--end-year", "2026"],
        ):
            with self.subTest(arguments=arguments), self.assertRaises(SystemExit) as error:
                collect_season.main(arguments)
            self.assertEqual(error.exception.code, 2)

    def test_cli_continues_after_year_failure_and_returns_incomplete_status(self):
        with patch.object(
            collect_season, "collect_season", side_effect=[RuntimeError("schedule unavailable"), None]
        ) as collect_year, patch.object(
            collect_season.collect_driver_constructor_stats, "collect_driver_constructor_stats"
        ) as collect_stats:
            status = collect_season.main([
                "--start-year", "2025", "--end-year", "2026"
            ])

        self.assertEqual(status, 1)
        self.assertEqual([call.args[0] for call in collect_year.call_args_list], [2025, 2026])
        collect_stats.assert_called_once_with()

    def test_unified_cli_collects_multiple_years_and_builds_combined_inventories(self):
        class FakeSession:
            def __init__(self, year):
                self.results = pd.DataFrame([{
                    "Abbreviation": f"D{year}",
                    "TeamName": f"Team {year}",
                    "TeamColor": "abcdef",
                }])
                self.laps = pd.DataFrame()
                self.weather_data = pd.DataFrame()
                self.race_control_messages = pd.DataFrame()

            def load(self, **kwargs):
                self.load_options = kwargs

        schedules = {
            year: pd.DataFrame([{
                "EventName": "Test Grand Prix",
                "Location": "Test Circuit",
                "RoundNumber": 1,
                "Session1": "Race",
                "Session1DateUtc": f"{year}-03-01T12:00:00Z",
            }])
            for year in (2025, 2026)
        }

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / "raw"
            processed = root / "processed"
            with patch.object(collect_season, "OUTPUT", raw), patch.object(
                collect_season, "CACHE", root / "cache"
            ), patch.object(collect_season, "_CACHE_ENABLED", True), patch.object(
                collect_season.fastf1,
                "get_event_schedule",
                side_effect=lambda year, include_testing: schedules[year],
            ), patch.object(
                collect_season.fastf1,
                "get_session",
                side_effect=lambda year, round_number, session_type: FakeSession(year),
            ), patch.object(collect_driver_constructor_stats, "RAW", raw), patch.object(
                collect_driver_constructor_stats, "PROCESSED", processed
            ):
                status = collect_season.main([
                    "--start-year", "2025", "--end-year", "2026", "--event", "Test Grand Prix"
                ])

            self.assertEqual(status, 0)
            drivers = pd.read_csv(processed / "drivers.csv")
            constructors = pd.read_csv(processed / "constructors.csv")
            self.assertEqual(set(drivers["Abbreviation"]), {"D2025", "D2026"})
            self.assertEqual(set(constructors["TeamName"]), {"Team 2025", "Team 2026"})

    def test_cli_rejects_non_numeric_year(self):
        with patch.object(collect_season, "collect_season") as collect_year:
            with self.assertRaises(SystemExit):
                collect_season.main(["--year", "not-a-year"])

        collect_year.assert_not_called()

    def test_session_failures_are_independent_and_auxiliary_data_is_saved(self):
        class FakeSession:
            def __init__(self, fail=False):
                self.fail = fail
                self.results = pd.DataFrame([{"Abbreviation": "AAA", "TeamName": "Team A"}])
                self.laps = pd.DataFrame([{"Driver": "AAA", "LapNumber": 1}])
                self.weather_data = pd.DataFrame([{"Time": pd.Timedelta(minutes=1), "TrackTemp": 30}])
                self.race_control_messages = pd.DataFrame([{"Time": pd.Timedelta(minutes=1), "Message": "Yellow flag"}])

            def load(self, **kwargs):
                if self.fail:
                    raise RuntimeError("session data unavailable")
                self.load_options = kwargs

        event = {
            "EventName": "Test Grand Prix",
            "Location": "Test Circuit",
            "RoundNumber": 1,
            "Session1": "Practice 1",
            "Session1DateUtc": "2025-03-01T08:00:00Z",
            "Session2": "Qualifying",
            "Session2DateUtc": "2025-03-01T12:00:00Z",
        }
        sessions = {"FP1": FakeSession(), "Q": FakeSession(fail=True)}
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(collect_season, "OUTPUT", Path(directory)), patch.object(
                collect_season, "_CACHE_ENABLED", True
            ), patch.object(collect_season.fastf1, "get_session", side_effect=lambda year, gp, code: sessions[code]):
                collected = collect_season.collect_event(
                    2025, "Test Grand Prix", event=event, now="2025-03-02T00:00:00Z"
                )
                self.assertEqual(collected, ["FP1"])
                self.assertTrue((Path(directory) / "2025_fp1_results.parquet").exists())
                self.assertTrue((Path(directory) / "2025_fp1_weather.parquet").exists())
                self.assertTrue((Path(directory) / "2025_fp1_messages.parquet").exists())
                self.assertFalse((Path(directory) / "2025_q_results.parquet").exists())
                weather = pd.read_parquet(Path(directory) / "2025_fp1_weather.parquet")
                self.assertEqual(weather.loc[0, "SessionType"], "FP1")
                self.assertEqual(sessions["FP1"].load_options["weather"], True)

    def test_future_session_does_not_replace_previously_collected_data(self):
        event = {
            "EventName": "Future Grand Prix",
            "RoundNumber": 1,
            "Session1": "Race",
            "Session1DateUtc": "2030-03-01T12:00:00Z",
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            existing = pd.DataFrame([{"Abbreviation": "AAA", "Position": 1}])
            existing.to_parquet(output / "2025_r_results.parquet", index=False)
            with patch.object(collect_season, "OUTPUT", output), patch.object(
                collect_season, "_CACHE_ENABLED", True
            ), patch.object(collect_season.fastf1, "get_session") as get_session:
                collected = collect_season.collect_event(
                    2025, "Future Grand Prix", event=event, now="2029-01-01T00:00:00Z"
                )
                get_session.assert_not_called()
            self.assertEqual(collected, [])
            saved = pd.read_parquet(output / "2025_r_results.parquet")
            self.assertEqual(saved.loc[0, "Abbreviation"], "AAA")


class MasterDataTests(unittest.TestCase):
    def test_master_data_reads_race_and_new_session_results(self):
        with tempfile.TemporaryDirectory() as directory:
            raw = Path(directory) / "raw"
            processed = Path(directory) / "processed"
            raw.mkdir()
            pd.DataFrame([{"Abbreviation": "AAA", "TeamName": "Team A", "TeamColor": "ff0000"}]).to_parquet(
                raw / "2025_race_results.parquet", index=False
            )
            pd.DataFrame([{"Abbreviation": "BBB", "TeamName": "Team B", "TeamColor": "00ff00"}]).to_parquet(
                raw / "2025_q_results.parquet", index=False
            )
            with patch.object(collect_driver_constructor_stats, "RAW", raw), patch.object(
                collect_driver_constructor_stats, "PROCESSED", processed
            ):
                collect_driver_constructor_stats.collect_driver_constructor_stats()
            drivers = pd.read_csv(processed / "drivers.csv")
            teams = pd.read_csv(processed / "constructors.csv")
            self.assertEqual(set(drivers["Abbreviation"]), {"AAA", "BBB"})
            self.assertEqual(set(teams["TeamName"]), {"Team A", "Team B"})

    def test_no_result_files_preserves_existing_inventories(self):
        with tempfile.TemporaryDirectory() as directory:
            raw = Path(directory) / "raw"
            processed = Path(directory) / "processed"
            raw.mkdir()
            processed.mkdir()
            drivers_path = processed / "drivers.csv"
            constructors_path = processed / "constructors.csv"
            drivers_path.write_text("Abbreviation\nAAA\n", encoding="utf-8")
            constructors_path.write_text("TeamName\nTeam A\n", encoding="utf-8")

            with patch.object(collect_driver_constructor_stats, "RAW", raw), patch.object(
                collect_driver_constructor_stats, "PROCESSED", processed
            ):
                refreshed = collect_driver_constructor_stats.collect_driver_constructor_stats()

            self.assertFalse(refreshed)
            self.assertEqual(drivers_path.read_text(encoding="utf-8"), "Abbreviation\nAAA\n")
            self.assertEqual(constructors_path.read_text(encoding="utf-8"), "TeamName\nTeam A\n")


if __name__ == "__main__":
    unittest.main()
