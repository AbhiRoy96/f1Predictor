"""Evaluation metrics and chronological backtesting for F1 race predictions."""

from typing import Any, Callable, Dict, List, Optional
from inspect import signature
from pathlib import Path
import json
import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.metrics import average_precision_score, brier_score_loss, f1_score, mean_absolute_error, precision_score, recall_score
from src.utils.logging import setup_logger

logger = setup_logger(__name__)


def compute_winner_metrics(
    y_true: pd.Series,
    y_prob: np.ndarray,
    groups: pd.Series,
) -> Dict[str, float]:
    """Calculate race-level winner metrics: Grouped Log Loss, Brier Score, Top-1 Accuracy, Top-3 Coverage."""
    df = pd.DataFrame({"y": y_true.values, "prob": y_prob, "group": groups.values})

    # Brier score
    brier = float(np.mean((df["prob"].values - df["y"].values) ** 2))

    # Grouped Log Loss, Top-1 Accuracy, Top-3 Coverage
    total_log_loss = 0.0
    top1_correct = 0
    top3_correct = 0
    num_races = 0

    for _, grp in df.groupby("group"):
        if grp["y"].sum() == 0:
            continue
        num_races += 1
        # Normalize probabilities per group to prevent log loss distortion
        p = np.clip(grp["prob"].values, 1e-7, 1.0 - 1e-7)
        p = p / np.sum(p)
        y = grp["y"].values

        total_log_loss -= np.sum(y * np.log(p))

        # Check winner prediction
        actual_winner_idx = grp["y"].idxmax()
        sorted_indices = grp["prob"].sort_values(ascending=False).index

        if sorted_indices[0] == actual_winner_idx:
            top1_correct += 1
        if actual_winner_idx in sorted_indices[:3]:
            top3_correct += 1

    grouped_log_loss = float(total_log_loss / max(num_races, 1))
    top1_acc = float(top1_correct / max(num_races, 1))
    top3_cov = float(top3_correct / max(num_races, 1))

    return {
        "grouped_log_loss": round(grouped_log_loss, 4),
        "brier_score": round(brier, 4),
        "top_1_accuracy": round(top1_acc, 4),
        "top_3_coverage": round(top3_cov, 4),
        "num_races_evaluated": num_races,
    }


def compute_podium_metrics(
    y_true: pd.Series,
    y_prob: np.ndarray,
    threshold: float = 0.5,
) -> Dict[str, float]:
    """Calculate podium classification metrics: Precision, Recall, F1, PR-AUC, Brier score."""
    y_t = y_true.values.astype(int)
    y_pred = (y_prob >= threshold).astype(int)

    precision = float(precision_score(y_t, y_pred, zero_division=0))
    recall = float(recall_score(y_t, y_pred, zero_division=0))
    f1 = float(f1_score(y_t, y_pred, zero_division=0))
    brier = float(brier_score_loss(y_t, y_prob))

    try:
        pr_auc = float(average_precision_score(y_t, y_prob))
    except Exception:
        pr_auc = 0.0

    return {
        "podium_precision": round(precision, 4),
        "podium_recall": round(recall, 4),
        "podium_f1": round(f1, 4),
        "podium_pr_auc": round(pr_auc, 4),
        "podium_brier_score": round(brier, 4),
    }


def compute_position_metrics(
    y_true: pd.Series,
    y_pred: np.ndarray,
    groups: Optional[pd.Series] = None,
) -> Dict[str, float]:
    """Calculate position regression & ranking metrics: MAE, Spearman rank correlation."""
    mae = float(mean_absolute_error(y_true, y_pred))

    if groups is not None:
        df = pd.DataFrame({"y_true": y_true.values, "y_pred": y_pred, "group": groups.values})
        corrs = []
        for _, grp in df.groupby("group"):
            if len(grp) > 2:
                corr, _ = spearmanr(grp["y_true"], grp["y_pred"])
                if not np.isnan(corr):
                    corrs.append(corr)
        spearman = float(np.mean(corrs)) if corrs else 0.0
    else:
        corr, _ = spearmanr(y_true, y_pred)
        spearman = float(corr) if not np.isnan(corr) else 0.0

    return {
        "position_mae": round(mae, 4),
        "spearman_rank_correlation": round(spearman, 4),
    }


def run_chronological_backtest(
    df: pd.DataFrame,
    model_factory: Callable[[], Any],
    feature_cols: List[str],
    target_col: str = "target_winner",
    min_train_seasons: int = 2,
) -> Dict[str, Any]:
    """Perform expanding-window chronological cross-validation by Season."""
    if "Year" not in df.columns:
        raise ValueError("Dataset missing 'Year' column for chronological backtesting")

    seasons = sorted(df["Year"].dropna().unique())
    if len(seasons) <= min_train_seasons:
        logger.warning("Not enough seasons for multi-split chronological backtest; running single split.")
        split_idx = max(1, len(seasons) - 1)
        train_seasons = seasons[:split_idx]
        test_seasons = seasons[split_idx:]
        season_splits = [(train_seasons, test_seasons)]
    else:
        season_splits = [
            (seasons[:i], [seasons[i]])
            for i in range(min_train_seasons, len(seasons))
        ]

    fold_results = []
    all_test_preds = []

    for train_years, test_years in season_splits:
        train_df = df[df["Year"].isin(train_years)].copy()
        test_df = df[df["Year"].isin(test_years)].copy()

        if train_df.empty or test_df.empty:
            continue

        train_df = train_df.sort_values(["Year", "RoundNumber"])
        test_df = test_df.sort_values(["Year", "RoundNumber"])
        X_train = train_df[feature_cols]
        y_train = train_df[target_col]
        X_test = test_df[feature_cols]
        y_test = test_df[target_col]

        model = model_factory()
        train_groups = train_df["Year"].astype(str) + "_" + train_df["RoundNumber"].astype(str)
        test_groups = test_df["Year"].astype(str) + "_" + test_df["RoundNumber"].astype(str)
        fit_kwargs = {"groups": train_groups} if "groups" in signature(model.fit).parameters else {}
        model.fit(X_train, y_train, **fit_kwargs)

        if hasattr(model, "predict_proba"):
            predict_kwargs = {"groups": test_groups} if "groups" in signature(model.predict_proba).parameters else {}
            probs = model.predict_proba(X_test, **predict_kwargs)
            p1 = probs[:, 1] if probs.shape[1] > 1 else probs[:, 0]
        else:
            p1 = model.predict(X_test)

        test_groups = test_df["Year"].astype(str) + "_" + test_df["RoundNumber"].astype(str)
        metrics = compute_winner_metrics(y_test, p1, test_groups)
        metrics["test_seasons"] = str(test_years)
        fold_results.append(metrics)

        test_df_pred = test_df.copy()
        test_df_pred["pred_prob"] = p1
        all_test_preds.append(test_df_pred)

    avg_metrics = {}
    if fold_results:
        for k in ["grouped_log_loss", "brier_score", "top_1_accuracy", "top_3_coverage"]:
            avg_metrics[f"mean_{k}"] = round(float(np.mean([f[k] for f in fold_results])), 4)

    return {
        "folds": fold_results,
        "summary": avg_metrics,
        "predictions": pd.concat(all_test_preds, ignore_index=True) if all_test_preds else pd.DataFrame(),
    }


def reliability(probabilities, outcomes, bins=10):
    """Observed bins only; unknown outcomes do not count as negative outcomes."""
    table = pd.DataFrame({"p": np.asarray(probabilities), "y": np.asarray(outcomes)}).dropna()
    table["bin"] = pd.cut(table.p, np.linspace(0, 1, bins + 1), include_lowest=True)
    return table.groupby("bin", observed=True).agg(
        n=("y", "size"), mean_p=("p", "mean"), hit_rate=("y", "mean")).reset_index()


def _baseline_strength(df, name):
    candidates = {
        "grid": ["grid_position", "GridPosition", "quali_position", "driver_qualifying_season_avg_position"],
        "championship": ["driver_race_season_points", "driver_race_career_points"],
        "team": ["constructor_race_season_points", "constructor_race_career_points"],
    }[name]
    column = next((c for c in candidates if c in df), None)
    if column is None:
        return np.zeros(len(df))
    values = pd.to_numeric(df[column], errors="coerce").replace([np.inf, -np.inf], np.nan)
    if name == "grid":
        values = values.where(values > 0)
        return -values.fillna((len(df) + 1) / 2).to_numpy(float) / 3
    # Fixed scaling prevents raw points from overwhelming the bounded temperature fit.
    return values.fillna(0).to_numpy(float) / (20 if name == "championship" else 40)


def _complete_race_metrics(frame):
    from scipy.stats import kendalltau
    from src.models.calibrate import pl_nll

    position = frame.target_position.to_numpy(float)
    winner = position == 1
    podium = position <= min(3, len(frame))
    top10 = position <= min(10, len(frame))
    p_win = frame.win_probability.to_numpy(float)
    p_podium = np.clip(frame.podium_probability.to_numpy(float), 1e-12, 1 - 1e-12)
    order = np.argsort(position)
    signed = frame.expected_position.to_numpy(float) - position
    predicted_top3 = np.argsort(-p_podium, kind="stable")[:min(3, len(frame))]
    true_top3 = np.flatnonzero(podium)
    spearman = spearmanr(position, frame.expected_position).statistic if len(frame) > 1 else np.nan
    kendall = kendalltau(position, frame.expected_position).statistic if len(frame) > 1 else np.nan
    dnf_known = frame.target_dnf.notna().to_numpy()
    dnf_brier = float(np.mean((frame.dnf_probability.to_numpy()[dnf_known]
                              - frame.target_dnf.to_numpy()[dnf_known]) ** 2)) if dnf_known.any() else np.nan
    ll = -pl_nll(frame.strength.to_numpy(), order, float(frame.pl_temperature.iloc[0]))
    return {
        "win_log_loss": float(-np.log(np.clip(p_win[winner][0], 1e-12, 1))),
        "win_brier": float(np.mean((p_win - winner) ** 2)),
        "top_1_accuracy": float(winner[np.argmax(p_win)]),
        "top_3_coverage": float(winner[np.argsort(-p_win)[:3]].any()),
        "podium_brier": float(np.mean((frame.podium_probability.to_numpy() - podium) ** 2)),
        "podium_log_loss": float(-np.mean(podium * np.log(p_podium) + (~podium) * np.log1p(-p_podium))),
        "top_3_overlap": float(len(np.intersect1d(predicted_top3, true_top3))),
        "spearman": float(spearman), "kendall_tau": float(kendall),
        "pl_log_likelihood": ll, "pl_nll": -ll,
        "position_mae": float(np.abs(signed).mean()),
        "position_median_absolute_error": float(np.median(np.abs(signed))),
        "position_signed_error": float(signed.mean()),
        "dnf_brier": dnf_brier, "top10_brier": float(np.mean((frame.top10_probability - top10) ** 2)),
    }


def run_race_walk_forward(df, min_train_races=30, refit_every=4, n_simulations=2000):
    """Shared expanding folds, in memory only.

    Returns DataFrames: predictions (race model), baselines (long, model=grid/
    championship/team), metrics (one row/model/race), summary (one row/model),
    folds (one row/refit), reliability (model/target/bin), driver_bias and team_bias.
    Reliability target-specific aliases reliability_win/podium/top10/dnf are included.
    Position errors are predicted minus actual; rolling bias uses the latest five races.
    """
    from src.models.calibrate import fit_pl_temperature, fit_temperature
    from src.models.race_model import RacePredictor, ShrinkageDNFModel, chronological_races, dnf_labels, race_groups
    from src.simulation.race_simulator import MonteCarloRaceSimulator

    if min_train_races < 1 or refit_every < 1 or n_simulations < 1:
        raise ValueError("Training races, refit cadence and simulation count must be positive")
    races = chronological_races(df)
    model_predictions, baseline_predictions, folds = [], [], []
    model = None
    baseline_calibration = {}
    dnf_model = None
    train_end = None
    fold = -1
    for i in range(min_train_races, len(races)):
        test = df.iloc[races[i]].reset_index(drop=True)
        if "Abbreviation" in test:
            test = test.sort_values("Abbreviation").reset_index(drop=True)
        if (i - min_train_races) % refit_every == 0:
            train = df.iloc[np.concatenate(races[:i])].reset_index(drop=True)
            model = RacePredictor().fit(train)
            dnf_model = ShrinkageDNFModel().fit(train)
            train_end = model.metadata_["training_end"]
            baseline_calibration = {}
            train_races = chronological_races(train)
            for name in ["grid", "championship", "team"]:
                scores = [_baseline_strength(train.iloc[rows], name) for rows in train_races]
                winners = [int(train.iloc[rows].target_position.to_numpy().argmin()) for rows in train_races]
                temperature = fit_temperature(scores, winners)
                orders = [np.argsort(train.iloc[rows].target_position.to_numpy()) for rows in train_races]
                pl_temperature = fit_pl_temperature([s / temperature for s in scores], orders)
                baseline_calibration[name] = {"temperature": temperature, "pl_temperature": pl_temperature,
                                               "train_races": i, "training_end": train_end}
            fold += 1
            folds.append({"fold": fold, "n_train_races": i,
                          "train_groups": race_groups(train).drop_duplicates().tolist(),
                          "train_max_utc": train_end,
                          "test_min_utc": str(test.SessionStartUtc.min()) if "SessionStartUtc" in test else race_groups(test).iloc[0],
                          "baseline_calibration": baseline_calibration.copy(),
                          "calibration_provenance": model.calibration_provenance_})
        prediction = model.predict(test, n_simulations=n_simulations)
        prediction["pl_temperature"] = model.pl_temperature_
        prediction["model"] = "race_model"
        outputs = [prediction]
        for name, calibration in baseline_calibration.items():
            strength = _baseline_strength(test, name) / calibration["temperature"]
            grid = test.copy()
            grid["Abbreviation"] = np.arange(len(test))
            baseline = MonteCarloRaceSimulator(n_simulations).simulate(
                grid, strength=strength, p_dnf=dnf_model.predict(test),
                pl_temperature=calibration["pl_temperature"])
            baseline = baseline.set_index("Abbreviation").reindex(np.arange(len(test))).reset_index(drop=True)
            # Keep identifying columns without allowing labels to enter prediction logic.
            for col in ["Year", "RoundNumber", "EventName", "SessionStartUtc", "Abbreviation", "TeamName", "team_lineage", "stage"]:
                if col in test:
                    baseline[col] = test[col].to_numpy()
            baseline["strength"] = strength
            baseline["pl_temperature"] = calibration["pl_temperature"]
            baseline["model"] = name
            baseline["model_version"] = "baseline_pl_v1"
            if "stage" not in baseline:
                baseline["stage"] = "pre_weekend"
            outputs.append(baseline)
        for output in outputs:
            output["target_position"] = test.target_position.to_numpy()
            output["target_winner"] = test.target_position.eq(1).to_numpy(int)
            output["target_podium"] = test.target_position.le(min(3, len(test))).to_numpy(int)
            output["target_top10"] = test.target_position.le(min(10, len(test))).to_numpy(int)
            output["target_dnf"] = dnf_labels(test)[0].to_numpy()
            output["fold"] = fold
            output["race_index"] = i
            output["training_end"] = train_end
            output["signed_position_error"] = output.expected_position - output.target_position
            (model_predictions if output.model.iloc[0] == "race_model" else baseline_predictions).append(output)
    predictions = pd.concat(model_predictions, ignore_index=True) if model_predictions else pd.DataFrame()
    baselines = pd.concat(baseline_predictions, ignore_index=True) if baseline_predictions else pd.DataFrame()
    combined = pd.concat([predictions, baselines], ignore_index=True)
    metric_rows, reliability_frames = [], []
    if not combined.empty:
        for (name, race_index), frame in combined.groupby(["model", "race_index"], sort=False):
            metric_rows.append({"model": name, "race_index": race_index, "fold": frame.fold.iloc[0],
                                "Year": frame.Year.iloc[0], "RoundNumber": frame.RoundNumber.iloc[0],
                                **_complete_race_metrics(frame)})
        for name, frame in combined.groupby("model", sort=False):
            for target, column in [("win", "win_probability"), ("podium", "podium_probability"),
                                   ("top10", "top10_probability"), ("dnf", "dnf_probability")]:
                label = "target_winner" if target == "win" else f"target_{target}"
                table = reliability(frame[column], frame[label])
                table["model"], table["target"] = name, target
                reliability_frames.append(table)
    metrics = pd.DataFrame(metric_rows)
    metric_names = [c for c in metrics if c not in {"model", "race_index", "fold", "Year", "RoundNumber"}]
    summary = metrics.groupby("model", sort=False)[metric_names].mean().reset_index() if metric_names else pd.DataFrame()
    if not summary.empty:
        grid_mae = summary.loc[summary.model.eq("grid"), "position_mae"].iloc[0]
        summary["position_mae_vs_grid"] = summary.position_mae - grid_mae
    reliability_table = pd.concat(reliability_frames, ignore_index=True) if reliability_frames else pd.DataFrame(
        columns=["bin", "n", "mean_p", "hit_rate", "model", "target"])
    bias = {}
    for kind, candidates in [("driver", ["Abbreviation", "DriverNumber"]), ("team", ["team_lineage", "TeamName"])]:
        entity = next((c for c in candidates if c in combined), None)
        table = pd.DataFrame()
        if entity:
            table = combined.groupby(["model", entity, "race_index"], as_index=False).agg(
                signed_error=("signed_position_error", "mean"))
            table = table.sort_values(["model", entity, "race_index"])
            table["rolling_signed_error"] = table.groupby(["model", entity]).signed_error.transform(
                lambda values: values.rolling(5, min_periods=1).mean())
        bias[f"{kind}_bias"] = table
    result = {"predictions": predictions, "baselines": baselines, "metrics": metrics,
              "summary": summary, "folds": pd.DataFrame(folds), "reliability": reliability_table, **bias}
    for target in ["win", "podium", "top10", "dnf"]:
        result[f"reliability_{target}"] = reliability_table[reliability_table.target.eq(target)].reset_index(drop=True)
    return result


def save_evaluation_reports(result, output_dir):
    """Explicit opt-in report writer; never writes production model artifacts."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name, frame in result.items():
        if isinstance(frame, pd.DataFrame):
            path = output_dir / f"{name}.csv"
            export = frame.copy()
            for col in export.select_dtypes(include="object"):
                export[col] = export[col].map(lambda value: json.dumps(value, default=str)
                                              if isinstance(value, (dict, list)) else value)
            export.to_csv(path, index=False)
            paths[name] = path
    return paths
