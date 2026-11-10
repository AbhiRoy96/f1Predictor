import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from streamlit.testing.v1 import AppTest

from src.models.race_model import RacePredictor
from src.utils import config
from tests.test_race_model import race_fixture

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
        source = PAGE.read_text(encoding="utf-8")
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

    def test_dashboard_renders_and_runs_scenario_with_saved_latent_inputs(self):
        frame = race_fixture(4)
        prediction = RacePredictor(n_estimators=3).fit(frame).predict(frame.iloc[-6:], n_simulations=100)
        prediction["DriverName"] = prediction.Abbreviation
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prediction.to_parquet(root / "predictions_race_2026_rnd4_post_quali.parquet", index=False)
            with patch.object(config, "PREDICTIONS_DATA_DIR", root):
                app = AppTest.from_file(str(PAGE)).run(timeout=30)
                self.assertEqual(len(app.exception), 0)
                self.assertFalse(app.button[0].disabled)
                app.button[0].click().run(timeout=30)
                self.assertEqual(len(app.exception), 0)
                self.assertEqual(app.dataframe[-1].value.Abbreviation.nunique(), 6)


if __name__ == "__main__":
    unittest.main()
