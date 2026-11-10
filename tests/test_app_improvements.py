import importlib.util
import unittest
from pathlib import Path

import pandas as pd

PAGE = Path(__file__).parents[1] / "app/pages/01_Race_Prediction.py"
SPEC = importlib.util.spec_from_file_location("race_prediction_page", PAGE)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Could not load {PAGE}")
PAGE_MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PAGE_MODULE)


class RacePredictionPageTests(unittest.TestCase):
    def test_position_distribution_is_long_and_ordered(self):
        predictions = pd.DataFrame({
            "Abbreviation": ["AAA", "BBB"],
            "DriverName": ["A Driver", "B Driver"],
            "prob_p2": [0.3, 0.7],
            "prob_p1": [0.7, 0.3],
        })
        result = PAGE_MODULE.position_distribution_frame(predictions)
        self.assertEqual(result.Position.tolist(), [1, 1, 2, 2])
        self.assertAlmostEqual(result.Probability.sum(), 2.0)

    def test_page_has_no_hard_coded_fake_importances(self):
        source = PAGE.read_text()
        self.assertNotIn("[0.34, 0.24, 0.18", source)
        self.assertIn("p10_position", source)
        self.assertIn("model_version", source)

    def test_evaluation_report_names_follow_pipeline_contract(self):
        metadata = pd.Series({
            "session": "race", "year": 2026, "round_number": 19, "stage": "post_quali"
        })
        paths = PAGE_MODULE.evaluation_report_paths(metadata)
        self.assertEqual(
            paths["summary"],
            Path("reports/walk_forward_race_2026_rnd19_post_quali_summary.parquet"),
        )


if __name__ == "__main__":
    unittest.main()
