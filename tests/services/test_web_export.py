"""Contract tests for the JSON consumed by the Next.js front end."""

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from scripts.export_web_data import build_demo_frame, build_run, export, main


class WebExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frame = build_demo_frame(n_simulations=2000)
        cls.payload = build_run(cls.frame, "demo", demo=True)

    def test_run_is_consistent_and_strictly_json(self):
        drivers = self.payload["drivers"]
        self.assertEqual(len(drivers), 22)
        self.assertAlmostEqual(sum(d["win_probability"] for d in drivers), 1)
        self.assertAlmostEqual(sum(d["podium_probability"] for d in drivers), 3)
        for driver in drivers:
            self.assertAlmostEqual(sum(driver["positionProbabilities"]), 1, places=4)
            self.assertEqual(len(driver["positionProbabilities"]), 22)
        json.dumps(self.payload, allow_nan=False)

    def test_demo_is_flagged_and_scenarios_replay_saved_inputs(self):
        self.assertTrue(self.payload["meta"]["demo"])
        self.assertEqual(self.payload["meta"]["model_version"], "illustrative_sample")
        self.assertEqual(set(self.payload["scenarios"]),
                         {"standard_dry", "wet_chaos", "high_safety_car", "high_degradation", "reliability_attrition"})
        self.assertEqual(self.payload["scenarios"]["standard_dry"]["kind"], "calibrated_reference")
        self.assertEqual(self.payload["scenarios"]["wet_chaos"]["kind"], "heuristic_sensitivity")
        standard = {d["Abbreviation"]: d["simulated_win_prob"] for d in self.payload["scenarios"]["standard_dry"]["drivers"]}
        saved = {d["Abbreviation"]: d["win_probability"] for d in self.payload["drivers"]}
        np.testing.assert_allclose([standard[k] for k in saved], list(saved.values()))

    def test_export_writes_manifest_and_requires_a_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = export(root / "out", root / "none", root / "reports", demo=True)
            self.assertEqual([entry["id"] for entry in manifest], ["demo"])
            self.assertTrue((root / "out/runs/demo.json").exists())
            self.assertEqual(main(["--output", str(root / "empty"), "--predictions-dir", str(root / "none"),
                                   "--reports-dir", str(root / "reports")]), 1)

    def test_empty_forecast_is_rejected(self):
        with self.assertRaises(ValueError):
            build_run(self.frame.iloc[0:0], "empty")


if __name__ == "__main__":
    unittest.main()
