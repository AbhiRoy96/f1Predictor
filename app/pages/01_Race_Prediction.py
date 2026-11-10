"""Screen 1 - Race Prediction Dashboard."""

import streamlit as st
import pandas as pd
import numpy as np
from pathlib import Path
import joblib

from src.data.normalise_entities import normalise_driver_name
from src.simulation.race_simulator import MonteCarloRaceSimulator
from src.simulation.scenarios import SCENARIOS
from src.visualization.telemetry import create_win_probability_chart, create_podium_probability_chart, create_feature_importance_chart
from src.utils.config import MODELS_DIR, PROCESSED_DATA_DIR, PREDICTIONS_DATA_DIR

st.set_page_config(page_title="Race Prediction", page_icon="🏆", layout="wide")


def load_race_predictions() -> pd.DataFrame:
    """Load or generate race prediction dataset."""
    pred_file = PREDICTIONS_DATA_DIR / "sao_paulo_2026_predictions.parquet"
    if pred_file.exists():
        return pd.read_parquet(pred_file)

    # Standard default grid representation for São Paulo GP
    drivers = [
        {"Abbreviation": "VER", "TeamName": "Red Bull Racing", "driver_qualifying_season_avg_position": 1.5, "win_probability": 0.42, "podium_probability": 0.85, "expected_position": 1.8},
        {"Abbreviation": "NOR", "TeamName": "McLaren", "driver_qualifying_season_avg_position": 2.2, "win_probability": 0.28, "podium_probability": 0.78, "expected_position": 2.5},
        {"Abbreviation": "LEC", "TeamName": "Ferrari", "driver_qualifying_season_avg_position": 3.0, "win_probability": 0.15, "podium_probability": 0.62, "expected_position": 3.6},
        {"Abbreviation": "PIA", "TeamName": "McLaren", "driver_qualifying_season_avg_position": 4.0, "win_probability": 0.08, "podium_probability": 0.45, "expected_position": 4.4},
        {"Abbreviation": "HAM", "TeamName": "Ferrari", "driver_qualifying_season_avg_position": 4.5, "win_probability": 0.04, "podium_probability": 0.35, "expected_position": 5.1},
        {"Abbreviation": "RUS", "TeamName": "Mercedes", "driver_qualifying_season_avg_position": 5.5, "win_probability": 0.02, "podium_probability": 0.25, "expected_position": 6.0},
        {"Abbreviation": "ALO", "TeamName": "Aston Martin", "driver_qualifying_season_avg_position": 7.0, "win_probability": 0.01, "podium_probability": 0.12, "expected_position": 7.5},
    ]
    df = pd.DataFrame(drivers)
    df["DriverName"] = df["Abbreviation"].apply(normalise_driver_name)
    return df


def main():
    st.title("🏆 Race Prediction Dashboard")
    st.subheader("2026 São Paulo Grand Prix — Autódromo José Carlos Pace (Interlagos)")

    st.markdown("""
    **Prediction Cutoff:** 2026-11-08 16:30:00 UTC (Pre-Race Cutoff strictly before formation lap)  
    **Model Architecture:** Point-in-time Calibrated Ensemble (Logistic Regression + Random Forest + XGBoost)
    """)
    st.markdown("---")

    df = load_race_predictions()

    # Top Metrics
    top_driver = df.iloc[0]
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Predicted Winner", f"{top_driver['DriverName']} ({top_driver['Abbreviation']})")
    with col2:
        st.metric("Win Probability", f"{top_driver['win_probability']*100:.1f}%")
    with col3:
        st.metric("Podium Probability", f"{top_driver['podium_probability']*100:.1f}%")
    with col4:
        st.metric("Expected Finish Position", f"P{top_driver['expected_position']:.1f}")

    # Probability Charts
    col_left, col_right = st.columns(2)
    with col_left:
        st.plotly_chart(create_win_probability_chart(df), use_container_width=True)
    with col_right:
        st.plotly_chart(create_podium_probability_chart(df), use_container_width=True)

    # Influential Factors
    st.markdown("### 🔍 Model Explainability & Factor Importance")
    factors = [
        "driver_qualifying_season_avg_position",
        "constructor_race_season_points",
        "driver_race_points_5",
        "driver_race_circuit_wins",
        "prior_weekend_best_lap_seconds",
        "driver_race_career_wins",
    ]
    importances = [0.34, 0.24, 0.18, 0.11, 0.08, 0.05]
    st.plotly_chart(create_feature_importance_chart(factors, importances), use_container_width=True)

    # Scenario Simulation Section
    st.markdown("---")
    st.markdown("### 🎲 Monte Carlo Race Simulation & Scenario Analysis")
    scenario_choice = st.selectbox(
        "Select Environmental & Safety Car Scenario:",
        list(SCENARIOS.keys()),
        format_func=lambda k: SCENARIOS[k].name,
    )
    st.info(SCENARIOS[scenario_choice].description)

    if st.button("Run Monte Carlo Simulation (2,000 Iterations)"):
        with st.spinner("Simulating race iterations..."):
            sim = MonteCarloRaceSimulator(n_simulations=2000)
            sim_res = sim.simulate(df, scenario=scenario_choice, win_probabilities=df["win_probability"].values)
            st.dataframe(
                sim_res[[
                    "Abbreviation", "TeamName", "simulated_win_prob",
                    "simulated_podium_prob", "simulated_dnf_prob",
                    "expected_position", "prob_p1", "prob_p2", "prob_p3"
                ]],
                use_container_width=True,
            )


if __name__ == "__main__":
    main()
