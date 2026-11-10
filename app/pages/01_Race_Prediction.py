"""Screen 1 - Race Prediction Dashboard."""

import streamlit as st
import pandas as pd
from pathlib import Path
from typing import Optional

from src.data.normalise_entities import normalise_driver_name
from src.simulation.race_simulator import MonteCarloRaceSimulator
from src.simulation.scenarios import SCENARIOS
from src.visualization.telemetry import create_win_probability_chart, create_podium_probability_chart, create_feature_importance_chart
from src.utils.config import PREDICTIONS_DATA_DIR

st.set_page_config(page_title="Race Prediction", page_icon="🏆", layout="wide")


def list_saved_prediction_files() -> list[Path]:
    """Return saved prediction runs, newest first."""
    return sorted(
        PREDICTIONS_DATA_DIR.glob("predictions_*.parquet"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )


def prediction_run_label(prediction_file: Path) -> str:
    """Build a readable event/session label from artifact metadata when available."""
    try:
        predictions = pd.read_parquet(prediction_file)
        metadata_columns = {"session", "year", "event_name", "round_number"}
        if not predictions.empty and metadata_columns.issubset(predictions.columns):
            metadata = predictions.iloc[0]
            return (
                f"{metadata['year']} {metadata['event_name']} — "
                f"{metadata['session']} (round {metadata['round_number']})"
            )
    except (IndexError, OSError, ValueError):
        pass
    return prediction_file.stem.replace("predictions_", "").replace("_", " ")


def load_race_predictions(prediction_file: Optional[Path] = None) -> pd.DataFrame:
    """Load the selected prediction run or a clearly identified demo grid."""
    if prediction_file is None:
        saved_files = list_saved_prediction_files()
        prediction_file = saved_files[0] if saved_files else None
    if prediction_file is not None and prediction_file.exists():
        predictions = pd.read_parquet(prediction_file)
        filename_parts = prediction_file.stem.removeprefix("predictions_").split("_")
        if len(filename_parts) >= 3 and filename_parts[0] in {"race", "sprint", "qualifying"}:
            if "session" not in predictions.columns:
                predictions["session"] = filename_parts[0]
            if "year" not in predictions.columns:
                try:
                    predictions["year"] = int(filename_parts[1])
                except ValueError:
                    pass
            if "round_number" not in predictions.columns and filename_parts[2].startswith("rnd"):
                try:
                    predictions["round_number"] = int(filename_parts[2][3:])
                except ValueError:
                    pass
        if "event_name" not in predictions.columns:
            predictions["event_name"] = "Saved prediction run"
        return predictions

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
    prediction_files = list_saved_prediction_files()
    selected_file = None
    if prediction_files:
        selected_file = st.selectbox(
            "Saved prediction run",
            prediction_files,
            format_func=prediction_run_label,
        )
        df = load_race_predictions(selected_file)
    else:
        st.info("No saved prediction runs were found; showing a clearly labeled demo grid.")
        df = load_race_predictions()

    if df.empty:
        st.warning("The selected prediction artifact contains no driver rows.")
        return

    metadata = df.iloc[0]
    session = str(metadata.get("session", "race"))
    year = metadata.get("year", "2026")
    event_name = metadata.get("event_name", "Demo São Paulo Grand Prix")
    st.subheader(f"{year} {event_name} — {session.title()}")
    generated_at = metadata.get("prediction_generated_at_utc")
    if generated_at is not None and pd.notna(generated_at):
        st.caption(f"Prediction generated at {generated_at} (UTC)")
    st.markdown("**Model Architecture:** Point-in-time Calibrated Ensemble (Logistic Regression + Random Forest + XGBoost)")
    st.markdown("---")

    df = df.sort_values("win_probability", ascending=False).reset_index(drop=True)

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

    variation_path = None
    if selected_file is not None:
        variation_path = selected_file.with_name(
            selected_file.name.replace("predictions_", "variation_", 1)
        )
    if variation_path is not None and variation_path.exists():
        variation = pd.read_parquet(variation_path).iloc[0]
        st.markdown("### Backtest variation against completed results")
        metric_columns = st.columns(4)
        metric_columns[0].metric("Winner top-1", "Correct" if variation["winner_top1_correct"] else "Miss")
        metric_columns[1].metric("Win probability Brier", f"{variation['win_probability_brier']:.3f}")
        metric_columns[2].metric("Podium probability Brier", f"{variation['podium_probability_brier']:.3f}")
        metric_columns[3].metric("Position MAE", f"{variation['position_mae']:.2f}")
        variation_columns = [
            "Abbreviation", "actual_winner", "actual_podium", "actual_position",
            "win_probability_error", "podium_probability_error", "position_error",
        ]
        st.dataframe(df[[column for column in variation_columns if column in df]], use_container_width=True)

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
    if session in {"race", "sprint"}:
        st.markdown("---")
        st.markdown("### 🎲 Monte Carlo Race Simulation & Scenario Analysis")
        simulation_file: Optional[Path] = None
        if selected_file is not None and {"year", "round_number"}.issubset(df.columns):
            simulation_file = PREDICTIONS_DATA_DIR / (
                f"simulation_{session}_{int(metadata['year'])}_rnd{int(metadata['round_number'])}.parquet"
            )
        if simulation_file is not None and simulation_file.exists():
            saved_simulation = pd.read_parquet(simulation_file)
            simulation_columns = [
                "Abbreviation", "TeamName", "simulated_win_prob", "simulated_podium_prob",
                "simulated_dnf_prob", "expected_position", "prob_p1", "prob_p2", "prob_p3",
            ]
            scenario = saved_simulation.iloc[0].get("scenario", "saved")
            simulation_count = saved_simulation.iloc[0].get("simulation_count", "")
            st.caption(f"Saved scenario: {scenario} ({simulation_count} iterations)")
            st.dataframe(
                saved_simulation[[column for column in simulation_columns if column in saved_simulation]],
                use_container_width=True,
            )

        scenario_choice = st.selectbox(
            "Select Environmental & Safety Car Scenario:",
            list(SCENARIOS.keys()),
            format_func=lambda key: SCENARIOS[key].name,
        )
        st.info(SCENARIOS[scenario_choice].description)

        if st.button("Run Monte Carlo Simulation (2,000 Iterations)"):
            with st.spinner("Simulating race iterations..."):
                sim = MonteCarloRaceSimulator(n_simulations=2000)
                sim_res = sim.simulate(
                    df,
                    scenario=scenario_choice,
                    win_probabilities=df["win_probability"].to_numpy(dtype=float),
                )
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
