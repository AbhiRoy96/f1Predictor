"""Correctness regressions from the v2 recommendations."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from os import utime
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

import main as pipeline
from scripts.audit_features import audit_features
from scripts.audit_features import main as audit_main
from src.data.outcomes import dnf_labels
from src.features.engineer_features import build_features
from src.models.evaluate import (
    _baseline_strength,
    _complete_race_metrics,
    bootstrap_race_metrics,
)
from src.simulation.race_simulator import simulate_race, summarize_positions
from tests.test_feature_improvements import result


def grid_results():
    return pd.DataFrame([
        result(session, day, driver=f"D{i:02}", position=i + 1,
               GridPosition=np.nan if session == "Q" else 20 - i, Status="Finished")
        for session, day in (("Q", 3), ("R", 4)) for i in range(20)
    ])


class PhaseOneRecommendationTests(unittest.TestCase):
    def test_grid_position_populated_with_flagged_qualifying_proxy(self):
        source = grid_results()
        table = build_features(source)["R"].sort_values("Abbreviation")
        self.assertGreater(table.grid_position.nunique(), 5)
        np.testing.assert_array_equal(table.grid_position, np.arange(1, 21))
        self.assertTrue(table.grid_is_quali_proxy.eq(1).all())
        self.assertTrue(table.grid_penalty_places_is_missing.eq(1).all())
        self.assertTrue(table.pit_lane_start_is_missing.eq(1).all())
        self.assertTrue((table.grid_source_max_utc < table.FeatureCutoffUtc).all())
        for stage in ("pre_weekend", "post_fp"):
            early = build_features(source, stage=stage)["R"]
            self.assertTrue(early.grid_position_is_missing.eq(1).all())
            self.assertTrue(early.grid_is_quali_proxy.eq(0).all())

    def test_proxy_never_overrides_an_eligible_official_grid(self):
        source = grid_results()
        source["GridPublishedAt"] = pd.Series(pd.NaT, index=source.index, dtype="datetime64[ns, UTC]")
        race = source.SessionType.eq("R")
        start = source.loc[race, "SessionStartUtc"].iloc[0]
        source.loc[race & source.Abbreviation.eq("D00"), "GridPublishedAt"] = start - pd.Timedelta(hours=2)
        source.loc[race & source.Abbreviation.eq("D01"), "GridPublishedAt"] = start
        source.loc[source.SessionType.eq("Q") & source.Abbreviation.eq("D02"), "GridPosition"] = 4
        source.loc[race & source.Abbreviation.eq("D03"), "GridPublishedAt"] = start - pd.Timedelta(hours=1)
        source.loc[race & source.Abbreviation.eq("D03"), "GridPosition"] = 0
        table = build_features(source)["R"].set_index("Abbreviation")
        self.assertEqual(table.loc["D00", "grid_position"], 20)
        self.assertEqual(table.loc["D00", "grid_is_quali_proxy"], 0)
        self.assertEqual(table.loc["D01", "grid_position"], 2)
        self.assertEqual(table.loc["D01", "grid_is_quali_proxy"], 1)
        self.assertEqual(table.loc["D02", "grid_position"], 4)
        self.assertEqual(table.loc["D02", "grid_is_quali_proxy"], 0)
        self.assertEqual(table.loc["D03", "pit_lane_start"], 1)
        self.assertEqual(table.loc["D03", "grid_position"], 21)
        self.assertEqual(table.loc["D03", "grid_is_quali_proxy"], 0)

    def test_grid_baseline_not_degenerate_and_quali_is_explicit(self):
        frame = pd.DataFrame({"grid_position": 10.5, "quali_position": np.arange(1, 21)})
        grid = _baseline_strength(frame, "grid")
        np.testing.assert_allclose(grid, _baseline_strength(frame, "quali"))
        self.assertGreater(np.ptp(grid), 0)
        positions, retired = simulate_race(grid, np.zeros(20), n_sims=5000, seed=7)
        prediction = summarize_positions(positions, retired)
        pipeline._validate_prediction_probabilities(prediction)
        self.assertLess(-np.log(prediction.win_probability.iloc[0]), np.log(20))
        frame["grid_position"] = np.arange(20, 0, -1)
        self.assertNotEqual(_baseline_strength(frame, "grid").argmax(), _baseline_strength(frame, "quali").argmax())
        frame["grid_position_is_missing"] = 1
        np.testing.assert_allclose(_baseline_strength(frame, "grid"), _baseline_strength(frame, "quali"))

    def test_dnf_lapped_is_finished_and_nonstarters_are_unknown(self):
        statuses = ["Lapped", "+1 Lap", "+2 Laps", "Finished", "Disqualified",
                    "Excluded", "Did not start", "Did not qualify", "Withdrew", None, "Engine", "Collision"]
        total, mechanical, collision = dnf_labels(pd.DataFrame({"Status": statuses, "ClassifiedPosition": "R"}))
        np.testing.assert_array_equal(total.iloc[:4], 0)
        self.assertTrue(total.iloc[4:9].isna().all())
        self.assertEqual(total.iloc[9], 1)
        self.assertTrue(mechanical.iloc[4:10].isna().all())
        np.testing.assert_array_equal(total.iloc[10:], 1)
        self.assertEqual(mechanical.iloc[10], 1)
        self.assertEqual(collision.iloc[11], 1)
        self.assertTrue(dnf_labels(pd.DataFrame({"Status": [None]}))[0].isna().all())

    def test_finisher_quantiles_exclude_retirements_and_leave_no_finishes_unknown(self):
        places = np.array([[1, 2, 3], [2, 1, 3], [2, 1, 3], [2, 1, 3]])
        retired = np.array([[False, False, True], [True, False, True],
                            [True, False, True], [True, False, True]])
        summary = summarize_positions(places, retired)
        self.assertEqual(summary.p90_position.iloc[0], 2)
        self.assertEqual(summary.p10_finish.iloc[0], 1)
        self.assertEqual(summary.p90_finish.iloc[0], 1)
        self.assertTrue(summary.loc[2, ["p10_finish", "p90_finish"]].isna().all())
        self.assertAlmostEqual(summary.win_probability.sum(), 1)
        self.assertAlmostEqual(summary.podium_probability.sum(), 3)

    def test_dnf_skill_unavailable_without_training_labels_or_reference_error(self):
        positions, retired = simulate_race([1, 0], [0, 0], n_sims=100)
        frame = summarize_positions(positions, retired).assign(
            target_position=[1, 2], target_dnf=0, strength=[1, 0], pl_temperature=1)
        self.assertTrue(np.isnan(_complete_race_metrics(frame)["dnf_brier_skill"]))
        frame["training_dnf_base_rate"] = 0
        self.assertEqual(_complete_race_metrics(frame)["dnf_constant_brier"], 0)
        self.assertTrue(np.isnan(_complete_race_metrics(frame)["dnf_brier_skill"]))

    def test_dnf_skill_bootstrap_uses_ratio_of_losses_and_paired_races(self):
        metrics = pd.DataFrame([
            {"model": model, "race_index": i, "dnf_brier": loss, "dnf_constant_brier": base,
             "dnf_brier_skill": 1 - loss / base}
            for model, losses in (("grid", [.2, .4]), ("race_model", [.1, .2]))
            for i, (loss, base) in enumerate(zip(losses * 4, [.1, .9] * 4))
        ])
        confidence = bootstrap_race_metrics(metrics, seed=8)
        model = confidence.query("model == 'race_model' and metric == 'dnf_brier_skill'")
        self.assertAlmostEqual(model.query("reference == 'absolute'").estimate.iloc[0], .7)
        self.assertAlmostEqual(model.query("reference == 'grid_difference'").estimate.iloc[0], .3)

    def test_raw_hash_uses_metadata_without_reading_payloads(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "raw.parquet"
            path.write_bytes(b"raw data")
            with patch.object(Path, "open", side_effect=AssertionError("Raw content must not be read")):
                before = pipeline._data_hash([path])
            path.write_bytes(b"updated raw data")
            self.assertNotEqual(before, pipeline._data_hash([path]))
            before = pipeline._data_hash([path])
            stat = path.stat()
            utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
            self.assertNotEqual(before, pipeline._data_hash([path]))
            code = Path(directory) / "source.py"
            code.write_text("before")
            before = pipeline._data_hash([code])
            stat = code.stat()
            code.write_text("after!")
            utime(code, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            self.assertNotEqual(before, pipeline._data_hash([code]))

    def test_terminal_uses_decimal_probabilities_and_conditional_intervals(self):
        stream = io.StringIO()
        with redirect_stdout(stream):
            pipeline._print_predictions(pd.DataFrame({
                "Abbreviation": ["PIA"], "win_probability": [.002], "p10_finish": [3.], "p90_finish": [12.]}), "race")
        self.assertIn("0.002", stream.getvalue())
        self.assertNotIn("e-0", stream.getvalue())
        self.assertIn("p90_finish", stream.getvalue())

    def test_audit_counts_raw_labels_and_detects_neutral_imputation(self):
        raw = grid_results()
        features = pd.DataFrame({
            "Year": 2026, "RoundNumber": 1, "SessionType": "R", "stage": "post_quali",
            "Abbreviation": [f"D{i:02}" for i in range(20)], "grid_position": 10.5,
            "grid_position_is_missing": 1, "quali_position": np.arange(1, 21),
            "driver_race_career_starts": 0})
        report = audit_features(features, raw)
        grid = report["feature_summary"].query("feature == 'grid_position'").iloc[0]
        self.assertEqual(grid.missing_fraction, 1)
        self.assertEqual(grid.distinct, 0)
        self.assertEqual(report["dnf_labels"].known.iloc[0], 20)
        self.assertEqual(report["latest_drivers"].newcomer.sum(), 20)
        self.assertTrue(report["issues"].issue.str.contains("Degenerate").any())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            features.to_parquet(root / "features.parquet")
            raw.to_parquet(root / "2026_r_results.parquet")
            output = root / "audit.json"
            with redirect_stdout(io.StringIO()):
                code = audit_main(["--features", str(root / "features.parquet"),
                                   "--raw-dir", str(root), "--output", str(output)])
            self.assertEqual(code, 1)
            self.assertIn("grid_by_race", json.loads(output.read_text()))
            features["grid_position"] = features.quali_position
            features["grid_position_is_missing"] = 0
            for column in ("quali_gap_to_pole_pct", "quali_gap_to_teammate_pct",
                           "fp2_long_run_pace_rank", "fp_best_lap_gap_pct"):
                features[column] = 0.
            features.to_parquet(root / "features.parquet")
            with redirect_stdout(io.StringIO()):
                self.assertEqual(audit_main(["--features", str(root / "features.parquet"),
                                             "--raw-dir", str(root), "--output", str(output)]), 0)


if __name__ == "__main__":
    unittest.main()
