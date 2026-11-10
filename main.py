"""F1 Race Intelligence & Prediction Pipeline Orchestrator."""

import argparse
from pathlib import Path
import tempfile
import joblib
import numpy as np
import pandas as pd

from src.data.collect_driver_constructor_stats import collect_driver_constructor_stats
from src.data.collect_season import _resolve_event, collect_event
from src.data.normalise_entities import normalise_driver_name
from src.data.validate_data import validate_features_df
from src.features.engineer_features import build_features, load_results
from src.models.train_podium import train_and_save_podium_model
from src.models.train_position import train_and_save_position_model
from src.models.train_winner import train_and_save_winner_models
from src.simulation.race_simulator import MonteCarloRaceSimulator
from src.utils.config import (
    PREDICTIONS_DATA_DIR,
    PROCESSED_DATA_DIR,
    RAW_DATA_DIR,
)
from src.utils.logging import setup_logger

logger = setup_logger("f1_orchestrator")
HISTORICAL_START_YEAR = 2021
SESSION_TYPES = {
    "race": ("R", "race"),
    "sprint": ("S", "sprint"),
    "qualifying": ("Q", "qualifying"),
}


def collect_historical_data(year: int, event: str) -> None:
    """Collect the requested event for each season from 2021 through the target year."""
    if year < HISTORICAL_START_YEAR:
        raise ValueError(f"Target year must be {HISTORICAL_START_YEAR} or later.")

    target_years = range(HISTORICAL_START_YEAR, year + 1)
    logger.info(f"Collecting event '{event}' for seasons {HISTORICAL_START_YEAR}-{year}")

    for collection_year in target_years:
        try:
            logger.info(f"Collecting event '{event}' for season {collection_year}...")
            collect_event(collection_year, event, raise_on_failure=False)
        except Exception as exc:
            logger.warning(f"Error collecting event '{event}' for season {collection_year}: {exc}")

    logger.info("Extracting and updating driver/constructor reference tables...")
    collect_driver_constructor_stats()
    logger.info("Data collection finished successfully.")


def ensure_processed_data(session: str, year: int, event: str) -> Path:
    """Collect history and build a point-in-time table for the requested session."""
    if session not in SESSION_TYPES:
        raise ValueError(f"Unsupported session {session!r}; choose race, sprint, or qualifying.")
    if year < HISTORICAL_START_YEAR:
        raise ValueError(f"Target year must be {HISTORICAL_START_YEAR} or later.")

    session_code, feature_name = SESSION_TYPES[session]
    PROCESSED_DATA_DIR.mkdir(parents=True, exist_ok=True)
    RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)
    collect_historical_data(year, event)

    logger.info(f"Building point-in-time {session} features from collected results...")
    results = load_results()
    result_years = pd.to_numeric(results["Year"], errors="coerce")
    historical_results = results.loc[
        result_years.between(HISTORICAL_START_YEAR, year)
    ].copy()
    features = build_features(historical_results)
    session_features = features.get(session_code)
    if session_features is None or session_features.empty:
        raise RuntimeError(
            f"No {session} results found for seasons {HISTORICAL_START_YEAR}-{year}."
        )

    feature_path = PROCESSED_DATA_DIR / f"{feature_name}_features.parquet"
    session_features.to_parquet(feature_path, index=False)
    logger.info(f"Saved {len(session_features)} {session} feature rows to {feature_path}")
    return feature_path


def _resolve_target_event(year: int, event: str):
    """Resolve an event alias to its canonical name and round from the saved schedule."""
    schedule_path = RAW_DATA_DIR / f"{year}_schedule.parquet"
    if not schedule_path.exists():
        raise RuntimeError(f"No event schedule is available for {year}; unable to resolve {event!r}.")

    schedule = pd.read_parquet(schedule_path)
    try:
        event_row = _resolve_event(schedule, year, event)
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    return int(event_row["RoundNumber"]), str(event_row["EventName"])


def _select_target_event(features, year, round_number, event_name, session):
    """Select only the requested event and session, failing instead of using a fallback."""
    session_code, _ = SESSION_TYPES[session]
    year_values = pd.to_numeric(features["Year"], errors="coerce")
    round_values = pd.to_numeric(features["RoundNumber"], errors="coerce")
    selected = features.loc[year_values.eq(year) & round_values.eq(round_number)].copy()
    if "SessionType" in selected:
        selected = selected.loc[selected["SessionType"].eq(session_code)].copy()
    if selected.empty:
        raise RuntimeError(
            f"No {session} session data found for {event_name} in {year} (round {round_number})."
        )
    return selected


def _validate_completed_target_results(target_event_df: pd.DataFrame) -> None:
    """Reject target sessions whose recorded outcomes are not complete enough to score."""
    target_columns = ["target_winner", "target_podium", "target_position"]
    missing_columns = [column for column in target_columns if column not in target_event_df]
    if missing_columns:
        raise ValueError(
            "Variation measurement requires completed target results; missing "
            f"columns: {', '.join(missing_columns)}."
        )

    targets = target_event_df[target_columns].apply(pd.to_numeric, errors="coerce")
    if targets.isna().any().any() or not np.isfinite(targets.to_numpy()).all():
        raise ValueError(
            "Variation measurement requires completed target results; one or more "
            "winner, podium, or finishing-position outcomes are missing."
        )
    if not targets["target_winner"].isin([0, 1]).all() or targets["target_winner"].sum() != 1:
        raise ValueError("Variation measurement requires exactly one completed event winner.")
    if not targets["target_podium"].isin([0, 1]).all():
        raise ValueError("Variation measurement requires valid completed podium outcomes.")
    if (targets["target_position"] <= 0).any():
        raise ValueError("Variation measurement requires valid completed finishing positions.")


def _prepare_backtest_data(features, year, round_number, event_name, session):
    """Return training rows and a validated held-out event for leakage-safe scoring."""
    held_out = _select_target_event(features, year, round_number, event_name, session)
    _validate_completed_target_results(held_out)

    year_values = pd.to_numeric(features["Year"], errors="coerce")
    round_values = pd.to_numeric(features["RoundNumber"], errors="coerce")
    target_mask = year_values.eq(year) & round_values.eq(round_number)
    if "SessionType" in features:
        session_code, _ = SESSION_TYPES[session]
        target_mask &= features["SessionType"].eq(session_code)
    training = features.loc[~target_mask].copy()

    if training.empty:
        raise ValueError("Not enough historical data remains to train after holding out the target event.")
    if training["target_winner"].dropna().nunique() < 2:
        raise ValueError("Not enough historical winner examples remain after holding out the target event.")
    if training["target_podium"].dropna().nunique() < 2:
        raise ValueError("Not enough historical podium examples remain after holding out the target event.")
    if not (pd.to_numeric(training["target_position"], errors="coerce") > 0).any():
        raise ValueError("Not enough historical finishing-position examples remain after the holdout.")
    return training, held_out


def _measure_prediction_variation(predictions: pd.DataFrame, actuals: pd.DataFrame):
    """Join actual outcomes to held-out predictions and calculate event-level errors."""
    outcome_columns = {
        "target_winner": "actual_winner",
        "target_podium": "actual_podium",
        "target_position": "actual_position",
    }
    actual_results = actuals[["Abbreviation", *outcome_columns]].rename(columns=outcome_columns)
    measured = predictions.merge(actual_results, on="Abbreviation", how="left", validate="one_to_one")
    if measured[list(outcome_columns.values())].isna().any().any():
        raise ValueError("Could not align all held-out predictions with their completed results.")

    measured["win_probability_error"] = measured["win_probability"] - measured["actual_winner"]
    measured["win_probability_absolute_error"] = measured["win_probability_error"].abs()
    measured["podium_probability_error"] = measured["podium_probability"] - measured["actual_podium"]
    measured["podium_probability_absolute_error"] = measured["podium_probability_error"].abs()
    measured["position_error"] = measured["expected_position"] - measured["actual_position"]
    measured["absolute_position_error"] = measured["position_error"].abs()

    summary = {
        "winner_top1_correct": int(
            measured.loc[measured["win_probability"].idxmax(), "actual_winner"] == 1
        ),
        "win_probability_brier": float(np.mean(measured["win_probability_error"] ** 2)),
        "podium_probability_brier": float(np.mean(measured["podium_probability_error"] ** 2)),
        "position_mae": float(measured["absolute_position_error"].mean()),
        "position_mean_signed_error": float(measured["position_error"].mean()),
        "driver_count": int(len(measured)),
    }
    return measured, summary


def _train_models(feature_path: Path, training_features=None):
    """Train the three models, optionally using an ephemeral holdout-free feature file."""
    if training_features is None:
        training_path = feature_path
        return (
            train_and_save_winner_models(training_path),
            train_and_save_podium_model(training_path),
            train_and_save_position_model(training_path),
        )

    with tempfile.TemporaryDirectory(prefix="backtest_training_", dir=PROCESSED_DATA_DIR) as directory:
        training_path = Path(directory) / feature_path.name
        training_features.to_parquet(training_path, index=False)
        return (
            train_and_save_winner_models(training_path),
            train_and_save_podium_model(training_path),
            train_and_save_position_model(training_path),
        )


def run_pipeline(
        session: str,
        year: int,
        event: str,
        measure_variation: bool = False,
):
    """Collect history and generate predictions for one requested event session."""
    logger.info("================================================================")
    logger.info("  FORMULA 1 RACE INTELLIGENCE & PREDICTION ENGINE")
    logger.info("================================================================")

    if session not in SESSION_TYPES:
        raise ValueError(f"Unsupported session {session!r}; choose race, sprint, or qualifying.")
    feature_path = ensure_processed_data(session, year, event)
    df_features = pd.read_parquet(feature_path)
    target_round, resolved_event_name = _resolve_target_event(year, event)
    target_event_df = _select_target_event(
        df_features, year, target_round, resolved_event_name, session
    )
    training_features = None
    if measure_variation:
        training_features, target_event_df = _prepare_backtest_data(
            df_features, year, target_round, resolved_event_name, session
        )

    # 2. Validation & Quality Checks
    session_code, _ = SESSION_TYPES[session]
    is_valid, errors = validate_features_df(df_features, session_type=session_code)
    logger.info(f"Data Contract Validation: {'PASSED' if is_valid else 'FAILED'} (Errors: {errors})")

    # 3. Model Training & Evaluation
    logger.info(f"--- Training {session.title()}, Podium, and Position Models ---")
    winner_artifacts, podium_artifacts, position_artifacts = _train_models(
        feature_path, training_features
    )

    # 4. Generate Predictions for the Target Event
    logger.info("--- Generating Predictions ---")
    model_obj = winner_artifacts["model_path"]
    model_dict = pd.read_pickle(model_obj) if str(model_obj).endswith(".pkl") else joblib.load(model_obj)
    winner_model = model_dict["model"]
    feat_cols = model_dict["feature_columns"]

    X_pred = target_event_df[feat_cols].fillna(0)
    groups_pred = pd.Series(f"{year}_{target_round}", index=target_event_df.index)

    win_probs = winner_model.predict_proba(X_pred, groups=groups_pred)[:, 1]
    target_event_df["win_probability"] = np.round(win_probs, 4)

    # Podium model prediction
    podium_dict = joblib.load(podium_artifacts["model_path"])
    podium_probs = podium_dict["model"].predict_proba(X_pred)[:, 1]
    target_event_df["podium_probability"] = np.round(podium_probs, 4)

    # Position model prediction
    pos_dict = joblib.load(position_artifacts["model_path"])
    exp_pos = pos_dict["model"].predict(X_pred)
    target_event_df["expected_position"] = np.round(exp_pos, 2)

    target_event_df["DriverName"] = target_event_df["Abbreviation"].apply(normalise_driver_name)
    predictions_df = target_event_df[[
        "Abbreviation", "DriverName", "TeamName", "win_probability", "podium_probability", "expected_position"
    ]].sort_values("win_probability", ascending=False).reset_index(drop=True)

    variation_summary = None
    if measure_variation:
        predictions_df, variation_summary = _measure_prediction_variation(
            predictions_df, target_event_df
        )

    generated_at = pd.Timestamp.now(tz="UTC")
    run_metadata = {
        "session": session,
        "year": year,
        "round_number": target_round,
        "event_name": resolved_event_name,
        "prediction_generated_at_utc": generated_at,
        "variation_measured": measure_variation,
    }
    for column, value in run_metadata.items():
        predictions_df[column] = value

    # Save predictions
    PREDICTIONS_DATA_DIR.mkdir(parents=True, exist_ok=True)
    pred_save_path = PREDICTIONS_DATA_DIR / f"predictions_{session}_{year}_rnd{target_round}.parquet"
    predictions_df.to_parquet(pred_save_path, index=False)
    logger.info(f"Predictions saved to {pred_save_path}")

    if variation_summary is not None:
        variation_path = PREDICTIONS_DATA_DIR / f"variation_{session}_{year}_rnd{target_round}.parquet"
        variation_row = {**run_metadata, **variation_summary}
        pd.DataFrame([variation_row]).to_parquet(variation_path, index=False)
        logger.info(f"Backtest variation saved to {variation_path}")

    print("\n=======================================================")
    print(f"🏆 {year} {resolved_event_name.upper()} {session.upper()} PREDICTIONS")
    print("=======================================================")
    print(predictions_df.to_string(index=False))

    if session in {"race", "sprint"}:
        n_simulations = 2000
        scenario = "standard_dry"
        logger.info(f"\n--- Running Monte Carlo Scenario Simulation ({scenario}) ---")
        sim = MonteCarloRaceSimulator(n_simulations=n_simulations)
        sim_results = sim.simulate(
            predictions_df,
            scenario=scenario,
            win_probabilities=predictions_df["win_probability"].to_numpy(dtype=float),
        )
        simulation_metadata = {
            "session": session,
            "year": year,
            "round_number": target_round,
            "event_name": resolved_event_name,
            "scenario": scenario,
            "simulation_count": n_simulations,
            "prediction_generated_at_utc": generated_at,
        }
        for column, value in simulation_metadata.items():
            sim_results[column] = value
        simulation_path = PREDICTIONS_DATA_DIR / f"simulation_{session}_{year}_rnd{target_round}.parquet"
        sim_results.to_parquet(simulation_path, index=False)
        logger.info(f"Simulation results saved to {simulation_path}")
        print(f"\n🎲 Monte Carlo Scenario: {scenario} ({n_simulations:,} Iterations):")
        print(
            sim_results[[
                "Abbreviation", "TeamName", "simulated_win_prob", "simulated_podium_prob",
                "simulated_dnf_prob", "expected_position",
            ]].to_string(index=False)
        )
        print("=======================================================\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Predict an F1 race, sprint, or qualifying session")
    parser.add_argument("session", choices=SESSION_TYPES, help="target session type")
    parser.add_argument("year", type=int, help="target season (2021 or later)")
    parser.add_argument("event", help="target round name, such as 'Brazilian Grand Prix'")
    parser.add_argument(
        "--measure-variation",
        action="store_true",
        help="backtest a completed event, excluding it from model training and saving prediction errors",
    )
    args = parser.parse_args(argv)

    if args.year < HISTORICAL_START_YEAR:
        parser.error(f"year must be {HISTORICAL_START_YEAR} or later")
    run_pipeline(
        args.session, args.year, args.event, measure_variation=args.measure_variation
    )


if __name__ == "__main__":
    main()
