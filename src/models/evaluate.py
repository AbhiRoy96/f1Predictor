"""Evaluation metrics and chronological backtesting for F1 race predictions."""

from typing import Any, Callable, Dict, List, Optional
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

        X_train = train_df[feature_cols].fillna(0)
        y_train = train_df[target_col]
        X_test = test_df[feature_cols].fillna(0)
        y_test = test_df[target_col]

        model = model_factory()
        model.fit(X_train, y_train)

        if hasattr(model, "predict_proba"):
            probs = model.predict_proba(X_test)
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
