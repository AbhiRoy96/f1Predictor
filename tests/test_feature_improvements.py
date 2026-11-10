"""Data/features integration contracts, independent of collection and model owners."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from src.data.normalise_entities import build_driver_lookup, constructor_lineage, normalise_constructor, normalise_driver_name
from src.data.validate_data import validate_features_df
from src.features import engineer_features as features
from src.utils.config import NON_FEATURE_COLUMNS, get_feature_columns


def result(session, day, driver="NEW", position=2, team="Audi", year=2026, round_number=1, **extra):
    return dict(Year=year, RoundNumber=round_number, SessionType=session,
                SessionStartUtc=pd.Timestamp(f"{year}-03-{day:02d}T12:00Z"),
                Abbreviation=driver, FullName="New Driver" if driver == "NEW" else "Team Mate",
                TeamName=team, CircuitName="interlagos", EventName="Test GP",
                Position=position, Points=0, **extra)


class FeatureImprovementTests(unittest.TestCase):
    def setUp(self):
        self.results = pd.DataFrame([
            result("R", 1, position=4, team="Kick Sauber", year=2025, Status="Finished", GridPosition=5),
            result("FP1", 1), result("FP1", 1, driver="MAT"),
            result("FP2", 2), result("FP2", 2, driver="MAT"),
            result("Q", 3, Q1=pd.Timedelta(seconds=91), GridPosition=3),
            result("Q", 3, driver="MAT", position=1, Q1=pd.Timedelta(seconds=90), GridPosition=1),
            result("R", 4, Status="Collision", GridPosition=3),
            result("R", 4, driver="MAT", position=1, Status="Finished", GridPosition=1),
        ])
        self.laps = pd.DataFrame([
            dict(Year=2026, RoundNumber=1, SessionType="FP2", SessionStartUtc=pd.Timestamp("2026-03-02T12:00Z"),
                 Driver=driver, LapTime=pd.Timedelta(seconds=seconds + lap / 10), Stint=1,
                 Compound="MEDIUM", TyreLife=lap + 1)
            for driver, seconds in (("NEW", 92), ("MAT", 90)) for lap in range(6)
        ])
        self.weather = pd.DataFrame([dict(Year=2026, RoundNumber=1, SessionType="FP2",
                                          SessionStartUtc=pd.Timestamp("2026-03-02T12:00Z"), TrackTemp=35)])
        self.messages = self.weather.drop(columns="TrackTemp").assign(Message="Safety Car deployed")

    def row(self, stage="post_quali", results=None, **kwargs):
        tables = features.build_features(self.results if results is None else results,
                                         self.laps, self.weather, self.messages, stage=stage, **kwargs)
        return tables["R"].query("Year == 2026 and Abbreviation == 'NEW'").iloc[0]

    def test_numeric_contract_rookie_and_lineage(self):
        table = features.build_features(self.results)["R"]
        rookie = table.query("Year == 2026 and Abbreviation == 'MAT'").iloc[0]
        position_columns = [column for column in get_feature_columns(table)
                            if ("avg_position" in column or "_form_" in column) and not column.endswith("_is_missing")]
        self.assertTrue(rookie[position_columns].gt(0).all())
        self.assertEqual(rookie["driver_race_career_avg_position_is_missing"], 1)
        audi = table.query("Year == 2026 and Abbreviation == 'NEW'").iloc[0]
        self.assertEqual(audi["constructor_race_career_sessions"], 1)
        self.assertEqual(audi["team_is_new_entry"], 0)
        self.assertEqual(audi["DriverName"], "New Driver")
        self.assertFalse(set(get_feature_columns(table)) & NON_FEATURE_COLUMNS)
        self.assertTrue(all(pd.api.types.is_numeric_dtype(table[column]) for column in get_feature_columns(table)))
        valid, errors = validate_features_df(table)
        self.assertTrue(valid, errors)

    def test_stages_and_qualifying_practice_features(self):
        pre, fp, quali = (self.row(stage) for stage in features.STAGES)
        self.assertEqual(pre["quali_position_is_missing"], 1)
        self.assertEqual(fp["quali_position_is_missing"], 1)
        self.assertEqual(pre["fp_best_lap_gap_pct_is_missing"], 1)
        self.assertGreater(fp["fp_best_lap_gap_pct"], 0)
        self.assertEqual(fp["fp2_long_run_pace_rank"], 2)
        self.assertEqual(quali["quali_position"], 2)
        self.assertEqual(quali["grid_position"], 3)
        self.assertAlmostEqual(quali["quali_gap_to_pole_pct"], (91 / 90 - 1) * 100)
        self.assertEqual(quali["grid_penalty_places"], -1)
        self.assertEqual(fp["prior_weekend_tracktemp_mean"], 35)
        for stage in features.STAGES:
            table = features.build_features(self.results, self.laps, self.weather, self.messages, stage=stage)["R"]
            valid, errors = validate_features_df(table)
            self.assertTrue(valid, errors)

    def test_point_in_time_full_vs_truncated_and_future_mutation(self):
        future = pd.DataFrame([result("Q", 8, position=1, round_number=2),
                               result("R", 9, position=1, round_number=2)])
        full = pd.concat([self.results, future], ignore_index=True)
        for stage in features.STAGES:
            pd.testing.assert_series_equal(self.row(stage), self.row(stage, results=full))
        # A Q result must not alter post-FP historical qualifying summaries either.
        changed = self.results.copy()
        changed.loc[changed.SessionType.eq("Q"), "Position"] = 19
        before, after = self.row("post_fp"), self.row("post_fp", results=changed)
        pd.testing.assert_series_equal(before.drop(labels=["target_dnf"]), after.drop(labels=["target_dnf"]))

    def test_auxiliary_requires_prior_identified_session(self):
        bad = self.weather.copy().drop(columns="SessionType").assign(TrackTemp=999)
        table = features.build_features(self.results, weather=bad)["R"]
        self.assertTrue(table["prior_weekend_tracktemp_mean"].isna().all())
        bad = self.weather.assign(SessionType="R", SessionStartUtc=pd.Timestamp("2026-03-04T12:00Z"), TrackTemp=999)
        table = features.build_features(self.results, weather=bad)["R"]
        self.assertTrue(table["prior_weekend_tracktemp_mean"].isna().all())
        for bad in (self.weather.assign(SessionType="UNKNOWN"),
                    self.weather.assign(SessionStartUtc=pd.Timestamp("2026-03-01T12:00Z")),
                    self.weather.assign(Date=pd.Timestamp("2026-03-04T13:00Z"))):
            table = features.build_features(self.results, weather=bad)["R"]
            self.assertTrue(table["prior_weekend_tracktemp_mean"].isna().all())

    def test_microsecond_timestamps_have_identical_cutoffs(self):
        converted = self.results.copy()
        converted["SessionStartUtc"] = converted["SessionStartUtc"].dt.as_unit("us")
        original, microseconds = self.row(), self.row(results=converted)
        columns = get_feature_columns(pd.DataFrame([original]))
        pd.testing.assert_series_equal(original[columns], microseconds[columns])

    def test_era_recency_and_shrinkage_use_only_prior_races(self):
        upcoming = pd.DataFrame([result("R", 9, position=19, round_number=2, Status="Engine")])
        data = pd.concat([self.results, upcoming], ignore_index=True)
        row = features.build_features(data)["R"].query("Year == 2026 and RoundNumber == 2").iloc[0]
        self.assertEqual(row["regulation_era"], 2026)
        self.assertEqual(row["rounds_into_era"], 1)
        self.assertAlmostEqual(row["driver_race_strength_current_weight"], 1 / 6)
        self.assertAlmostEqual(row["driver_race_strength_position"], 2 / 6 + 4 * 5 / 6)
        self.assertLess(row["driver_race_ewma_position"], 3)
        self.assertEqual(row["target_dnf_mechanical"], 1)
        self.assertEqual(row["target_dnf_collision"], 0)
        self.assertGreater(row["driver_incident_rate_shrunk"], .02)

    def test_unknown_outcomes_and_grid_publication(self):
        upcoming = self.results.copy()
        mask = upcoming.Year.eq(2026) & upcoming.SessionType.eq("R")
        upcoming.loc[mask, ["Position", "Points", "Status"]] = np.nan
        row = self.row(results=upcoming)
        self.assertTrue(pd.isna(row["target_dnf"]))
        self.assertTrue(pd.isna(row["target_winner"]))
        self.assertTrue(pd.isna(row["target_dnf_mechanical"]))
        self.assertEqual(self.row()["target_dnf_collision"], 1)
        # A target's final grid without provenance must not override earlier Q data.
        upcoming.loc[mask, "GridPosition"] = 20
        self.assertEqual(self.row(results=upcoming)["grid_position"], 3)
        upcoming.loc[mask, "GridPublishedAt"] = "2026-03-04T10:00Z"
        self.assertEqual(self.row(results=upcoming)["grid_position"], 20)
        upcoming.loc[mask, "GridPosition"] = 0
        pit = self.row(results=upcoming)
        self.assertEqual(pit["pit_lane_start"], 1)
        self.assertGreater(pit["grid_position"], 0)

    def test_validator_rejects_later_stage_and_future_sources(self):
        table = features.build_features(self.results, self.laps, stage="post_fp")["R"]
        table["quali_position"] = 1
        table["quali_position_is_missing"] = 0
        self.assertFalse(validate_features_df(table)[0])
        table = features.build_features(self.results)["R"]
        table["history_source_max_utc"] = table["SessionStartUtc"]
        self.assertFalse(validate_features_df(table)[0])
        with self.assertRaises(ValueError):
            features.build_features(self.results, stage="during_race")

    def test_names_and_new_constructor(self):
        data = pd.DataFrame([dict(Abbreviation="CAM", FullName="Data Supplied Name", BroadcastName="C NAME"),
                             dict(Abbreviation="CAM", FullName=np.nan, BroadcastName=np.nan)])
        self.assertEqual(normalise_driver_name("CAM", build_driver_lookup(data)), "Data Supplied Name")
        self.assertEqual(normalise_constructor("Haas F1 Team"), "Haas")
        self.assertEqual(normalise_constructor("RB"), "Racing Bulls")
        self.assertEqual(constructor_lineage("Audi"), constructor_lineage("Alfa Romeo"))
        self.assertNotEqual(constructor_lineage("Audi"), constructor_lineage("Cadillac"))
        new_team = self.results.copy()
        new_team.loc[new_team.Year.eq(2026), "TeamName"] = "Cadillac"
        row = self.row(results=new_team)
        self.assertEqual(row["team_is_new_entry"], 1)
        self.assertEqual(row["constructor_race_strength_current_weight"], 0)

    def test_load_feature_inputs_contract(self):
        with tempfile.TemporaryDirectory(dir=".") as directory, patch.object(features, "RAW", Path(directory)):
            self.laps.to_parquet(Path(directory) / "2026_fp2_laps.parquet")
            inputs = features.load_feature_inputs()
            self.assertEqual(set(inputs), {"laps", "weather", "messages", "news"})
            self.assertEqual(len(inputs["laps"]), len(self.laps))
            features.build_features(self.results, stage="post_fp", **inputs)

    def test_qualifying_gap_compares_the_same_phase_not_mixed_q1_q3_conditions(self):
        data = self.results.copy()
        data["Q2"] = pd.Series(pd.NaT, index=data.index, dtype="timedelta64[ns]")
        data["Q3"] = pd.Series(pd.NaT, index=data.index, dtype="timedelta64[ns]")
        own = data.SessionType.eq("Q") & data.Abbreviation.eq("NEW")
        mate = data.SessionType.eq("Q") & data.Abbreviation.eq("MAT")
        data.loc[own, "Q2"] = pd.Timedelta(seconds=101)
        data.loc[mate, "Q2"] = pd.Timedelta(seconds=100)
        data.loc[mate, "Q3"] = pd.Timedelta(seconds=70)
        row = self.row(results=data)
        self.assertEqual(row["quali_reference_phase"], 2)
        self.assertAlmostEqual(row["quali_gap_to_pole_pct"], 1.0)

    def test_dirty_practice_laps_do_not_improve_pace(self):
        laps = self.laps.assign(Deleted=False, IsAccurate=True, TrackStatus="1", PitInTime=pd.NaT)
        baseline = features.build_features(self.results, laps=laps)["R"].query("Year == 2026 and Abbreviation == 'NEW'").iloc[0]
        dirty = []
        for column, value in (("Deleted", True), ("IsAccurate", False), ("TrackStatus", "2"),
                              ("PitInTime", pd.Timedelta(seconds=50))):
            lap = laps.iloc[0].copy()
            lap["LapTime"] = pd.Timedelta(seconds=1)
            lap[column] = value
            dirty.append(lap)
        augmented = pd.concat([laps, pd.DataFrame(dirty)], ignore_index=True)
        row = features.build_features(self.results, laps=augmented)["R"].query("Year == 2026 and Abbreviation == 'NEW'").iloc[0]
        for column in ("fp_best_lap_gap_pct", "fp2_long_run_pace_rank", "fp2_long_run_pace_sample_count"):
            self.assertEqual(row[column], baseline[column])

    def test_nonoverlapping_track_time_cohorts_do_not_invent_long_run_comparability(self):
        laps = self.laps.copy()
        laps["Time"] = pd.to_timedelta(np.where(laps.Driver.eq("NEW"), 60, 1860), unit="s")
        row = features.build_features(self.results, laps=laps)["R"].query("Year == 2026 and Abbreviation == 'NEW'").iloc[0]
        self.assertEqual(row["fp2_long_run_pace_rank_is_missing"], 1)
        self.assertEqual(row["fp2_long_run_pace_sample_count"], 0)

    def test_era_form_resets_without_erasing_career_evidence(self):
        row = self.row("pre_weekend")
        self.assertEqual(row["driver_race_career_starts"], 1)
        self.assertEqual(row["driver_race_era_starts"], 0)
        self.assertEqual(row["driver_race_form_5_is_missing"], 1)
        self.assertEqual(row["driver_race_era_strength_position"], features.NEUTRAL_POSITION)
        table = features.build_features(self.results, laps=self.laps)["R"]
        self.assertNotIn("prior_weekend_best_lap_seconds", get_feature_columns(table))
        self.assertNotIn("prior_weekend_mean_lap_seconds", get_feature_columns(table))


if __name__ == "__main__":
    unittest.main()
