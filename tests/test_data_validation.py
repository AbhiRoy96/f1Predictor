"""Unit tests for data validation, contract checks, and entity normalization."""

import unittest
import pandas as pd
from src.data.normalise_entities import (
    normalise_circuit,
    normalise_constructor,
    normalise_driver_name,
)
from src.data.validate_data import generate_quality_report, validate_features_df, validate_results_df


class TestDataValidation(unittest.TestCase):
    def test_entity_normalization(self):
        self.assertEqual(normalise_constructor("red bull"), "Red Bull Racing")
        self.assertEqual(normalise_constructor("stake f1 team kick sauber"), "Kick Sauber")
        self.assertEqual(normalise_driver_name("VER"), "Max Verstappen")
        self.assertEqual(normalise_driver_name("HAM"), "Lewis Hamilton")
        self.assertEqual(normalise_circuit("interlagos"), "Autódromo José Carlos Pace")

    def test_validate_results_df(self):
        valid_df = pd.DataFrame([
            {"Year": 2026, "RoundNumber": 1, "SessionType": "R", "Abbreviation": "VER", "Position": 1},
            {"Year": 2026, "RoundNumber": 1, "SessionType": "R", "Abbreviation": "NOR", "Position": 2},
        ])
        is_valid, errors = validate_results_df(valid_df)
        self.assertTrue(is_valid)
        self.assertEqual(len(errors), 0)

        # Invalid: missing required column
        invalid_df = pd.DataFrame([{"Year": 2026}])
        is_valid, errors = validate_results_df(invalid_df)
        self.assertFalse(is_valid)
        self.assertGreater(len(errors), 0)

    def test_validate_features_df(self):
        valid_features = pd.DataFrame([
            {"Year": 2026, "RoundNumber": 1, "SessionStartUtc": pd.Timestamp("2026-03-01", tz="UTC"), "Abbreviation": "VER", "TeamName": "Red Bull", "target_winner": 1, "target_podium": 1, "target_position": 1},
            {"Year": 2026, "RoundNumber": 1, "SessionStartUtc": pd.Timestamp("2026-03-01", tz="UTC"), "Abbreviation": "NOR", "TeamName": "McLaren", "target_winner": 0, "target_podium": 1, "target_position": 2},
        ])
        is_valid, errors = validate_features_df(valid_features, session_type="R")
        self.assertTrue(is_valid)
        self.assertEqual(len(errors), 0)


if __name__ == "__main__":
    unittest.main()
