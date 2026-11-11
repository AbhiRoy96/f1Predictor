"""Screen 1 - Race Prediction Dashboard."""

from pathlib import Path
from typing import Optional

import pandas as pd
import plotly.express as px
import streamlit as st

from src.data.normalise_entities import normalise_driver_name
from src.simulation.race_simulator import simulate_saved_prediction
from src.simulation.scenarios import SCENARIOS
from src.utils.config import PREDICTIONS_DATA_DIR
from src.visualization.telemetry import create_win_probability_chart, create_podium_probability_chart

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
        {"Abbreviation": "VER", "TeamName": "Red Bull Racing", "driver_qualifying_season_avg_position": 1.5,
         "win_probability": 0.42, "podium_probability": 0.85, "expected_position": 1.8},
        {"Abbreviation": "NOR", "TeamName": "McLaren", "driver_qualifying_season_avg_position": 2.2,
         "win_probability": 0.28, "podium_probability": 0.78, "expected_position": 2.5},
        {"Abbreviation": "LEC", "TeamName": "Ferrari", "driver_qualifying_season_avg_position": 3.0,
         "win_probability": 0.15, "podium_probability": 0.62, "expected_position": 3.6},
        {"Abbreviation": "PIA", "TeamName": "McLaren", "driver_qualifying_season_avg_position": 4.0,
         "win_probability": 0.08, "podium_probability": 0.45, "expected_position": 4.4},
        {"Abbreviation": "HAM", "TeamName": "Ferrari", "driver_qualifying_season_avg_position": 4.5,
         "win_probability": 0.04, "podium_probability": 0.35, "expected_position": 5.1},
        {"Abbreviation": "RUS", "TeamName": "Mercedes", "driver_qualifying_season_avg_position": 5.5,
         "win_probability": 0.02, "podium_probability": 0.25, "expected_position": 6.0},
        {"Abbreviation": "ALO", "TeamName": "Aston Martin", "driver_qualifying_season_avg_position": 7.0,
         "win_probability": 0.01, "podium_probability": 0.12, "expected_position": 7.5},
    ]
    df = pd.DataFrame(drivers)
    df["DriverName"] = df["Abbreviation"].apply(normalise_driver_name)
    return df


def position_distribution_frame(predictions: pd.DataFrame) -> pd.DataFrame:
    """Return long-form position probabilities for plotting."""
    columns = sorted(
        (column for column in predictions if isinstance(column, str)
         and column.startswith("prob_p") and column[6:].isdigit()),
        key=lambda column: int(column[6:]),
    )
    if not columns:
        return pd.DataFrame(columns=["Driver", "Position", "Probability"])
    driver = predictions.get("DriverName", predictions["Abbreviation"]).astype(str)
    frame = predictions.assign(Driver=driver).melt(
        id_vars="Driver", value_vars=columns, var_name="Position", value_name="Probability"
    )
    frame["Position"] = frame["Position"].str.removeprefix("prob_p").astype(int)
    return frame


def evaluation_report_paths(metadata: pd.Series) -> dict[str, Path]:
    """Resolve reports produced for the selected run, when evaluation exists."""
    required = ("session", "year", "round_number", "stage")
    if any(pd.isna(metadata.get(column)) for column in required):
        return {}
    prefix = (
        f"walk_forward_{metadata['session']}_{int(metadata['year'])}_"
        f"rnd{int(metadata['round_number'])}_{metadata['stage']}"
    )
    reports = Path("reports")
    return {name: reports / f"{prefix}_{name}.parquet"
            for name in ("summary", "confidence", "reliability", "driver_bias", "team_bias")}


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
    st.markdown(
        "**Model Architecture:** conditional-finisher strength ranker + retirement model + final-distribution calibration")
    provenance = {
        "Stage": metadata.get("stage", "unknown"),
        "Model": metadata.get("model_version", "demo"),
        "Training start": metadata.get("training_start_utc", metadata.get("training_start", "unknown")),
        "Training end": metadata.get("training_end_utc", metadata.get("training_end", "unknown")),
        "Training races": metadata.get("training_races", metadata.get("n_train_races", "unknown")),
        "Scenario": metadata.get("scenario", "demo"),
        "Samples": metadata.get("simulation_count", "unknown"),
    }
    st.caption(" | ".join(f"{key}: {value}" for key, value in provenance.items()))
    st.caption(
        "P10-P90 ranges describe simulated finishing outcomes, not parameter confidence intervals. "
        "p10_finish and p90_finish condition on the driver not retiring; missing values mean no simulated finishes. "
        "MC standard errors measure numerical sampling error only.")
    if metadata.get("training_races", metadata.get("n_train_races", 0)) < 30:
        st.warning("Limited historical evidence: probabilities and calibration need more held-out races before they can be trusted.")
    if metadata.get("history_scope") == "available_collected_sessions_not_full_championship":
        st.caption("History covers collected events only. Season points/form are sampled-history summaries, not complete championship standings.")
    st.markdown("---")

    df = df.sort_values("win_probability", ascending=False).reset_index(drop=True)

    # Top Metrics
    top_driver = df.iloc[0]
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Predicted Winner", f"{top_driver['DriverName']} ({top_driver['Abbreviation']})")
    with col2:
        st.metric("Win Probability", f"{top_driver['win_probability'] * 100:.1f}%")
    with col3:
        st.metric("Podium Probability", f"{top_driver['podium_probability'] * 100:.1f}%")
    with col4:
        st.metric("Expected Finish Position", f"P{top_driver['expected_position']:.1f}")

    uncertainty_columns = [
        "DriverName", "Abbreviation", "TeamName", "win_probability", "podium_probability",
        "top10_probability", "expected_position", "median_position", "p10_position",
        "p90_position", "p10_finish", "p90_finish", "dnf_probability", "vs_grid_order",
        "win_probability_mcse",
    ]
    st.markdown("### Race prediction and uncertainty")
    st.dataframe(df[[column for column in uncertainty_columns if column in df]], use_container_width=True)

    distribution = position_distribution_frame(df)
    if not distribution.empty:
        st.markdown("### Finishing-position distribution")
        figure = px.bar(
            distribution, x="Driver", y="Probability", color="Position",
            title="Probability mass by finishing position", barmode="stack",
        )
        st.plotly_chart(figure, use_container_width=True)

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

    reports = {name: path for name, path in evaluation_report_paths(metadata).items() if path.exists()}
    if reports:
        st.markdown("### Walk-forward model evaluation")
        if "summary" in reports:
            st.caption("Model and baseline performance over chronological held-out races")
            st.dataframe(pd.read_parquet(reports["summary"]), use_container_width=True)
        if "confidence" in reports:
            with st.expander("Race-block bootstrap intervals and paired differences versus grid"):
                st.caption("Intervals are omitted with fewer than five held-out races; these are not guarantees of future skill.")
                st.dataframe(pd.read_parquet(reports["confidence"]), use_container_width=True)
        if "reliability" in reports:
            with st.expander("Probability reliability by bin"):
                st.dataframe(pd.read_parquet(reports["reliability"]), use_container_width=True)
        bias_columns = st.columns(2)
        for container, name, label in zip(bias_columns, ("driver_bias", "team_bias"), ("Driver bias", "Team bias")):
            if name in reports:
                container.caption(label)
                container.dataframe(pd.read_parquet(reports[name]), use_container_width=True)

    # Probability Charts
    col_left, col_right = st.columns(2)
    with col_left:
        st.plotly_chart(create_win_probability_chart(df), use_container_width=True)
    with col_right:
        st.plotly_chart(create_podium_probability_chart(df), use_container_width=True)

    # Scenario Simulation Section
    if session in {"race", "sprint"}:
        st.markdown("---")
        st.markdown("### 🎲 Derived Race Simulation & Scenario Analysis")
        st.caption(
            "Scenario outputs reuse saved strengths, retirement risks, temperature, seed and simulation count. "
            "They are not an independent prediction.")
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
        if scenario_choice != "standard_dry":
            st.warning("Heuristic sensitivity analysis: weather, safety-car and degradation multipliers are not learned or separately calibrated.")
        required_inputs = {"strength", "base_dnf_probability", "pl_temperature", "simulation_count", "seed",
                           "race_retirement_correlation"}
        can_simulate = required_inputs.issubset(df.columns)
        if not can_simulate:
            st.info("Regenerate this legacy/demo forecast with the current model to enable consistent scenario analysis.")
        if st.button("Run scenario with saved simulation settings", disabled=not can_simulate):
            with st.spinner("Simulating race iterations..."):
                sim_res = simulate_saved_prediction(df, scenario_choice)
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
