"""Offline evidence guards and model artifact compatibility."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import joblib
import numpy as np
import pandas as pd

from src.models.artifacts import dump_artifact, load_artifact
from src.models.calibrate import top10_pl_nll
from src.models.evaluate import bootstrap_race_metrics
from src.models.race_model import RacePredictor, recency_weights
from src.models.readiness import lock_experiment, run_ablation, run_final_test, training_window
from tests.models.test_race_model import race_fixture


class ReadinessTests(unittest.TestCase):
    def test_artifacts_require_exact_versions(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            path = Path(directory) / "model.joblib"
            artifact = {"model": "fixture"}
            dump_artifact(artifact, path)
            self.assertEqual(load_artifact(path)["model"], "fixture")
            artifact["metadata"]["library_versions"]["numpy"] = "incompatible"
            joblib.dump(artifact, path)
            with self.assertRaisesRegex(ValueError, "Incompatible"):
                load_artifact(path)
            joblib.dump({"model": "legacy"}, path)
            with self.assertRaisesRegex(ValueError, "missing version"):
                load_artifact(path)

    def test_calendar_decay_is_not_event_count_decay(self):
        frame = pd.DataFrame({"Year": [2026, 2026, 2026], "RoundNumber": [1, 2, 3],
                              "SessionStartUtc": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-07-01"], utc=True)})
        weights = recency_weights(frame, half_life_days=180)
        self.assertAlmostEqual(weights[0] / weights[1], 2 ** (-1 / 180))
        self.assertLess(weights[1], .51)
        self.assertEqual(weights[2], 1)

    def test_training_windows_are_calendar_based_and_strictly_past(self):
        frame = pd.DataFrame({"SessionStartUtc": pd.to_datetime(
            ["2024-02-29", "2025-02-28", "2026-02-27", "2026-02-28"], utc=True)})
        self.assertEqual(training_window(frame, "2026-02-28", 12).index.tolist(), [1, 2])
        self.assertEqual(training_window(frame, "2026-02-28").index.tolist(), [0, 1, 2])
        with self.assertRaises(ValueError):
            training_window(frame, "2026-02-28", 6)

    def test_locked_test_runner_fits_once_using_only_pre_cutoff_races(self):
        from src.models.evaluate import run_race_walk_forward
        frame = race_fixture(9)
        cutoff = frame.loc[frame.RoundNumber.eq(6), "SessionStartUtc"].iloc[0]
        with patch("src.models.race_model.RacePredictor",
                   side_effect=lambda: RacePredictor(n_estimators=3)):
            result = run_race_walk_forward(frame, min_train_races=2, refit_every=len(frame) + 1,
                                          window_months=12, evaluation_start=cutoff, n_simulations=30)
        self.assertEqual(len(result["folds"]), 1)
        self.assertEqual(result["folds"].n_train_races.iloc[0], 5)
        self.assertEqual(set(result["predictions"].RoundNumber), {6, 7, 8, 9})
        self.assertTrue((pd.to_datetime(result["predictions"].training_end, utc=True) < cutoff).all())
        self.assertEqual(set(result["best_baseline_metrics"].race_index),
                         set(result["predictions"].race_index))

    def test_top10_pl_keeps_tail_in_denominators(self):
        scores = np.linspace(2, -2, 12)
        order = np.arange(12)
        loss = top10_pl_nll(scores, order)
        swapped = order.copy()
        swapped[-2:] = swapped[-2:][::-1]
        self.assertAlmostEqual(loss, top10_pl_nll(scores, swapped))
        scores[-1] += 5
        self.assertGreater(top10_pl_nll(scores, order), loss)
        with self.assertRaises(ValueError):
            top10_pl_nll(scores, [0, 0])

    def test_paired_best_baseline_uses_same_races(self):
        metrics = pd.DataFrame([{"model": model, "race_index": race, "loss": race + offset}
                                for race in range(6) for model, offset in [("race_model", 1), ("grid", 3)]])
        best = metrics.loc[metrics.model.eq("grid")].copy()
        best["loss"] -= 1
        result = bootstrap_race_metrics(metrics, best_baseline_metrics=best)
        row = result.query("reference == 'best_baseline_difference'").iloc[0]
        self.assertEqual(row.estimate, -1)
        self.assertAlmostEqual(row.ci_low, -1)
        with self.assertRaisesRegex(ValueError, "identical"):
            bootstrap_race_metrics(metrics, best_baseline_metrics=best.iloc[:-1])

    @staticmethod
    def _fake_result(frame, **options):
        starts = pd.to_datetime(frame.SessionStartUtc, utc=True)
        if options.get("evaluation_start") is not None:
            frame = frame.loc[starts >= pd.to_datetime(options["evaluation_start"], utc=True)]
        races = frame[["Year", "RoundNumber"]].drop_duplicates()
        metrics = pd.DataFrame({"model": "race_model", "race_index": races.RoundNumber.to_numpy(),
                                "win_log_loss": 1.0, "podium_log_loss": .2,
                                "top10_log_loss": .1, "conditional_top10_pl_nll": .3})
        return {"metrics": metrics, "predictions": metrics[["race_index"]]}

    def test_ablation_never_sees_final_and_final_is_consumed_once(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            path, lock = root / "features.parquet", root / "lock.json"
            race_fixture(20).to_parquet(path, index=False)
            lock_experiment(path, lock, "2026-04-01", "2026-02-01", strength_model="legacy")
            seen = []

            def evaluate(frame, **options):
                seen.append((frame.copy(), options))
                return self._fake_result(frame, **options)

            with patch("src.models.evaluate.run_race_walk_forward", side_effect=evaluate):
                run_ablation(lock, root / "ablation")
                self.assertEqual(len(seen), 5)
                self.assertTrue(all(pd.to_datetime(frame.SessionStartUtc, utc=True).max()
                                    < pd.Timestamp("2026-04-01", tz="UTC") for frame, _ in seen))
                evidence = run_final_test(lock, root / "final")
                self.assertEqual(evidence["status"], "evaluated_once")
                self.assertGreater(seen[-1][1]["refit_every"], len(seen[-1][0]))
                with self.assertRaises(FileExistsError):
                    run_final_test(lock, root / "second-final")
                with self.assertRaisesRegex(ValueError, "consumed"):
                    run_ablation(lock, root / "second-ablation")
            self.assertTrue((root / "final" / "evidence.json").is_file())

    def test_locked_data_and_selection_cannot_change(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            path, lock = root / "features.parquet", root / "lock.json"
            frame = race_fixture(20)
            frame.to_parquet(path, index=False)
            lock_experiment(path, lock, "2026-04-01", "2026-02-01")
            with self.assertRaises(FileExistsError):
                lock_experiment(path, lock, "2026-04-01", "2026-02-01")
            frame["target_position"] = 7 - frame.target_position
            frame.to_parquet(path, index=False)
            with self.assertRaisesRegex(ValueError, "data changed"):
                run_ablation(lock, root / "ablation")


if __name__ == "__main__":
    unittest.main()
