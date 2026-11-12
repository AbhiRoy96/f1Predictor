"""Official-grid phase must use explicit published evidence, never a proxy."""

from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

import main as pipeline
from src.features.engineer_features import build_features
from src.utils.config import get_feature_columns
from tests.pipeline.test_phase_one_recommendations import grid_results


class OfficialGridTests(unittest.TestCase):
    @staticmethod
    def target():
        now = pd.Timestamp.now(tz="UTC")
        published = now - pd.Timedelta(hours=2)
        return pd.DataFrame({
            "Year": 2026, "RoundNumber": 1, "EventName": "Test", "SessionType": "R",
            "Abbreviation": ["AAA", "BBB", "CCC"],
            "SessionStartUtc": now + pd.Timedelta(days=1),
            "FeatureCutoffUtc": now + pd.Timedelta(days=1),
            "GridPosition": [2., 1., 0.], "GridPublishedAt": published,
            "grid_position": [2., 1., 4.], "grid_position_is_missing": 0,
            "grid_is_quali_proxy": 0, "grid_source_max_utc": published})

    def test_explicit_grid_and_documented_pit_lane_start_are_accepted(self):
        target = self.target()
        pipeline._validate_official_grid(target, pd.Timestamp.now(tz="UTC"))
        with self.assertRaisesRegex(ValueError, "ALL target entrants"):
            pipeline._validate_official_grid(target.iloc[:0], pd.Timestamp.now(tz="UTC"))

    def test_every_entrant_requires_eligible_explicit_and_actually_used_grid(self):
        cases = {
            "missing_raw_grid": ("GridPosition", np.nan),
            "negative_raw_grid": ("GridPosition", -1.),
            "fractional_raw_grid": ("GridPosition", 1.5),
            "out_of_field_grid": ("GridPosition", 9.),
            "missing_publication": ("GridPublishedAt", pd.NaT),
            "proxy": ("grid_is_quali_proxy", 1),
            "unknown_proxy_status": ("grid_is_quali_proxy", np.nan),
            "imputed": ("grid_position_is_missing", 1),
            "missing_effective_grid": ("grid_position", np.nan),
            "neutral_imputation": ("grid_position", 10.5),
            "wrong_effective_grid": ("grid_position", 3.),
            "missing_actual_source": ("grid_source_max_utc", pd.NaT),
            "unrelated_actual_source": ("grid_source_max_utc", pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=2)),
            "missing_cutoff": ("FeatureCutoffUtc", pd.NaT),
        }
        for name, (column, value) in cases.items():
            with self.subTest(name=name):
                target = self.target()
                target.loc[1, column] = value
                with self.assertRaisesRegex(ValueError, "ALL target entrants.*BBB"):
                    pipeline._validate_official_grid(target, pd.Timestamp.now(tz="UTC"))

    def test_publication_must_precede_cutoff_and_not_exceed_claimed_source_time(self):
        target = self.target()
        published = target.GridPublishedAt.iloc[0]
        # A source timestamp equal to actual publication is eligible.
        pipeline._validate_official_grid(target, published)
        with self.assertRaisesRegex(ValueError, "source_published_at_utc"):
            pipeline._validate_official_grid(target, published - pd.Timedelta(seconds=1))
        target["FeatureCutoffUtc"] = published
        with self.assertRaisesRegex(ValueError, "FeatureCutoffUtc"):
            pipeline._validate_official_grid(target, pd.Timestamp.now(tz="UTC"))
        target["FeatureCutoffUtc"] = published - pd.Timedelta(seconds=1)
        with self.assertRaises(ValueError):
            pipeline._validate_official_grid(target, pd.Timestamp.now(tz="UTC"))

    def test_old_feature_cache_without_explicit_columns_is_rejected(self):
        target = self.target().drop(columns="GridPublishedAt")
        with self.assertRaisesRegex(ValueError, "missing columns.*GridPublishedAt"):
            pipeline._validate_official_grid(target, pd.Timestamp.now(tz="UTC"))

    def test_pipeline_rejects_proxy_before_any_training_or_prediction(self):
        target = self.target()
        target.loc[2, "grid_is_quali_proxy"] = 1
        with patch.object(pipeline, "ensure_processed_data", return_value=Path("features.parquet")), patch.object(
                pipeline.pd, "read_parquet", return_value=target), patch.object(
                pipeline, "validate_features_df", return_value=(True, [])), patch.object(
                pipeline, "_resolve_target_event", return_value=(1, "Test")), patch.object(
                pipeline, "_model_artifact") as train, patch.object(pipeline, "_train_models") as backtest:
            with self.assertRaisesRegex(ValueError, "official_grid.*CCC"):
                pipeline.run_pipeline("race", 2026, "Test", forecast_phase="official_grid",
                                      source_published_at_utc=pd.Timestamp.now(tz="UTC").isoformat(),
                                      skip_collection=True)
        train.assert_not_called()
        backtest.assert_not_called()

    def test_feature_evidence_is_metadata_and_proxy_still_works_for_ordinary_phase(self):
        source = grid_results()
        races = source.SessionType.eq("R")
        start = source.loc[races, "SessionStartUtc"].iloc[0]
        source["GridPublishedAt"] = pd.Series(pd.NaT, index=source.index, dtype="datetime64[ns, UTC]")
        source.loc[races, "GridPublishedAt"] = start - pd.Timedelta(hours=2)
        target = build_features(source, stage="post_quali")["R"]
        pipeline._validate_official_grid(target, start - pd.Timedelta(hours=1))
        self.assertNotIn("GridPosition", get_feature_columns(target))
        self.assertNotIn("GridPublishedAt", get_feature_columns(target))
        self.assertTrue(target.GridPosition.notna().all())
        self.assertTrue(target.GridPublishedAt.notna().all())
        source.loc[races, "GridPublishedAt"] = pd.NaT
        proxy = build_features(source, stage="post_quali")["R"]
        self.assertTrue(proxy.grid_is_quali_proxy.eq(1).all())
        with self.assertRaisesRegex(ValueError, "official_grid"):
            pipeline._validate_official_grid(proxy, start - pd.Timedelta(hours=1))


if __name__ == "__main__":
    unittest.main()
