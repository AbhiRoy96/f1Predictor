"""One strength model and retirement model feeding coherent race simulations."""

import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar
from scipy.special import expit, logit, logsumexp, ndtri
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from xgboost import XGBRanker

from src.data.outcomes import dnf_labels
from src.models.calibrate import fit_simulation_temperature
from src.simulation.race_simulator import MonteCarloRaceSimulator, session_scenario
from src.utils.config import METADATA_COLUMNS, get_feature_columns
from src.utils.logging import setup_logger

logger = setup_logger(__name__)
MODEL_VERSION = "race_pl_v2"
STRENGTH_MODELS = ("legacy", "bayesian", "ensemble")
DEFAULT_STRENGTH_MODEL = "ensemble"
MODEL_VERSIONS = {"legacy": MODEL_VERSION, "bayesian": "race_bayesian_laplace_v1",
                  "ensemble": "race_bayesian_xgb_v1"}
LOGIT_FEATURES = ["grid_position", "quali_position", "quali_gap_to_pole_pct",
                  "driver_race_form_5", "driver_race_era_strength_position",
                  "driver_race_season_avg_position", "driver_race_season_points_per_start",
                  "constructor_race_season_points_per_start",
                  "driver_qualifying_season_avg_position"]


class NeutralRaceImputer(SimpleImputer):
    """Retain empty features and missing flags without interpreting missing form as P0."""

    def __init__(self, neutral_position=10.5):
        super().__init__(strategy="median", keep_empty_features=True, add_indicator=True)
        self.neutral_position = neutral_position

    def fit(self, X, y=None):
        super().fit(X, y)
        for i, column in enumerate(X.columns):
            if X[column].isna().all() and ("position" in column.lower() or "form_" in column.lower()):
                self.statistics_[i] = self.neutral_position
        return self


def race_groups(df):
    if not {"Year", "RoundNumber"}.issubset(df.columns):
        raise ValueError("Race rows require Year and RoundNumber")
    if df[["Year", "RoundNumber"]].isna().any().any():
        raise ValueError("Race keys cannot be missing")
    return df.Year.astype(str) + "_" + df.RoundNumber.astype(str)


def chronological_races(df):
    """Return positional row arrays, strictly ordered by actual race start when supplied."""
    groups = race_groups(df).to_numpy()
    table = df[["Year", "RoundNumber"]].copy().reset_index(drop=True)
    table["group"] = groups
    if "SessionStartUtc" in df:
        table["start"] = pd.to_datetime(df.SessionStartUtc, utc=True, errors="coerce").to_numpy()
        if table.start.isna().any() or table.groupby("group").start.nunique().max() != 1:
            raise ValueError("Each race requires one valid SessionStartUtc")
        races = table.drop_duplicates("group").sort_values(["start", "Year", "RoundNumber"])
        if races.start.duplicated().any():
            raise ValueError("Race starts must be strictly ordered")
    else:
        races = table.drop_duplicates("group").sort_values(["Year", "RoundNumber"])
    return [np.flatnonzero(groups == group) for group in races.group]


def recency_weights(df):
    """Thirty-event half-life; prior regulation eras retain one-quarter weight."""
    races = chronological_races(df)
    weights = np.empty(len(df), dtype=float)
    era = np.select([df.Year.ge(2026), df.Year.ge(2022)], [2026, 2022], default=2014)
    latest_era = era[races[-1][0]]
    for i, rows in enumerate(races):
        weights[rows] = 2 ** (-(len(races) - 1 - i) / 30)
    return weights * np.where(era == latest_era, 1, .25)


class ShrinkageDNFModel:
    """Beta-binomial entity shrinkage plus conservative regularized contextual odds."""

    def __init__(self, prior_starts=20):
        self.prior_starts = prior_starts

    @staticmethod
    def _entities(df, kind):
        columns = ["team_lineage", "TeamName"] if kind == "mechanical" else ["Abbreviation", "DriverNumber"]
        column = next((c for c in columns if c in df), None)
        return df[column].fillna("Unknown").astype(str) if column else pd.Series("Unknown", index=df.index)

    def fit(self, df):
        total, mechanical, collision = dnf_labels(df)
        other = (total - mechanical.fillna(0) - collision.fillna(0)).clip(0, 1)
        weights = pd.Series(recency_weights(df), index=df.index)
        self.models_ = {}
        self.context_columns_ = [c for c in get_feature_columns(df)
                                 if any(key in c.lower() for key in ["rain", "grid_position", "circuit", "temperature"])][:8]
        for kind, labels, default in [("mechanical", mechanical, 0.03), ("collision", collision, 0.025),
                                       ("other", other, 0.005)]:
            valid = labels.notna()
            prior = float(((labels[valid] * weights[valid]).sum() + 20 * default)
                          / (weights[valid].sum() + 20))
            entities = self._entities(df, kind)
            observed = pd.DataFrame({"entity": entities.to_numpy(), "label": labels.to_numpy(),
                                     "weight": weights.to_numpy()}).dropna()
            observed["weighted_label"] = observed.label * observed.weight
            stats = observed.groupby("entity").agg(sum=("weighted_label", "sum"), count=("weight", "sum"))
            rates = ((stats["sum"] + self.prior_starts * prior) / (stats["count"] + self.prior_starts)).to_dict()
            context = None
            if self.context_columns_ and valid.sum() >= 40 and labels[valid].sum() >= 5 and labels[valid].nunique() == 2:
                context = make_pipeline(NeutralRaceImputer(),
                                        StandardScaler(), LogisticRegression(C=0.05, max_iter=500))
                context.fit(df.loc[valid, self.context_columns_].replace([np.inf, -np.inf], np.nan),
                            labels[valid], logisticregression__sample_weight=weights[valid])
            self.models_[kind] = {"prior": prior, "rates": rates, "context": context,
                                  "context_prior": float(np.average(labels[valid], weights=weights[valid])) if valid.any() else prior,
                                  "n_labels": int(valid.sum())}
        self.n_total_labels_ = int(total.notna().sum())
        self.total_fallback_ = None
        if self.n_total_labels_ and not mechanical.eq(1).any() and not collision.eq(1).any() and total.eq(1).any():
            # Unknown failure causes are not false mechanical/collision labels.
            valid = total.notna()
            prior = float(((total[valid] * weights[valid]).sum() + 20 * .06) / (weights[valid].sum() + 20))
            rates = {}
            for kind in ["mechanical", "collision"]:
                observed = pd.DataFrame({"entity": self._entities(df, kind).to_numpy(),
                                         "label": total.to_numpy(), "weight": weights.to_numpy()}).dropna()
                observed["weighted_label"] = observed.label * observed.weight
                stats = observed.groupby("entity").agg(sum=("weighted_label", "sum"), count=("weight", "sum"))
                rates[kind] = ((stats["sum"] + self.prior_starts * prior) / (stats["count"] + self.prior_starts)).to_dict()
            self.total_fallback_ = {"prior": prior, "rates": rates}
        probabilities = np.clip(self.predict(df), 1e-5, 1 - 1e-5)
        density = np.exp(-.5 * ndtri(probabilities) ** 2) / np.sqrt(2 * np.pi)
        numerator, denominator, observed_races = 0., 0., 0
        for rows in chronological_races(df):
            rows = rows[total.iloc[rows].notna().to_numpy()]
            if len(rows) < 2:
                continue
            residual = total.iloc[rows].to_numpy() - probabilities[rows]
            numerator += residual.sum() ** 2 - (residual ** 2).sum()
            denominator += density[rows].sum() ** 2 - (density[rows] ** 2).sum()
            observed_races += 1
        # Shrunk first-order Gaussian-copula moment estimate of common race risk.
        estimate = max(0., numerator / denominator) if denominator > 0 else 0.
        self.race_correlation_ = float(min(.8, estimate) * observed_races / (observed_races + 20))
        return self

    def predict_components(self, df):
        if self.total_fallback_ is not None:
            fallback = self.total_fallback_
            total = np.mean([self._entities(df, kind).map(fallback["rates"][kind]).fillna(fallback["prior"]).to_numpy(float)
                             for kind in ["mechanical", "collision"]], axis=0)
            return {"mechanical": np.zeros(len(df)), "collision": np.zeros(len(df)), "other": total}
        result = {}
        for kind, model in self.models_.items():
            rate = self._entities(df, kind).map(model["rates"]).fillna(model["prior"]).to_numpy(float)
            if model["context"] is not None:
                context_p = model["context"].predict_proba(
                    df.reindex(columns=self.context_columns_).replace([np.inf, -np.inf], np.nan))[:, 1]
                offset = logit(np.clip(context_p, 1e-5, 1 - 1e-5)) - logit(np.clip(model["context_prior"], 1e-5, 1 - 1e-5))
                rate = expit(logit(np.clip(rate, 1e-5, 1 - 1e-5)) + 0.25 * offset)
            result[kind] = rate
        scale = np.maximum(sum(result.values()), 1)
        return {kind: rate / scale for kind, rate in result.items()}

    def predict(self, df):
        risks = self.predict_components(df)
        return sum(risks.values())


class RacePredictor:
    """Regularized ranker/logit blend; all calibrators see forward-held-out scores only."""

    def __init__(self, random_seed=42, n_estimators=100, oof_splits=4, strength_model="legacy"):
        if strength_model not in STRENGTH_MODELS:
            raise ValueError(f"Unknown strength model {strength_model!r}; choose {STRENGTH_MODELS}")
        self.random_seed = random_seed
        self.n_estimators = n_estimators
        self.oof_splits = oof_splits
        self.strength_model = strength_model

    def _fit_strength(self, df, races):
        if self.strength_model in {"bayesian", "ensemble"}:
            from src.models.bayesian_strength import DynamicBayesianStrength

            context = [c for c in LOGIT_FEATURES + ["fp2_long_run_pace_rank", "fp_best_lap_gap_pct",
                                                   "fp_teammate_gap_pct"] if c in self.feature_columns_]
            bayesian = DynamicBayesianStrength().fit(df, context)
            if self.strength_model == "bayesian":
                return bayesian
            from src.models.strength_ensemble import (
                BayesianXGBComponents,
                training_score_scale,
            )

            xgb = self._fit_legacy_strength(df, races, include_logistic=False)
            return BayesianXGBComponents(
                bayesian, xgb, bayesian.training_scale(df),
                training_score_scale(self._scores(df, xgb)[:, 0], df))
        return self._fit_legacy_strength(df, races)

    def _fit_legacy_strength(self, df, races, include_logistic=True):
        neutral_position = float(np.median([len(rows) + 1 for rows in races]) / 2)
        imputer = NeutralRaceImputer(neutral_position)
        features = df.reindex(columns=self.feature_columns_).replace([np.inf, -np.inf], np.nan)
        x = imputer.fit_transform(features)
        dnf = dnf_labels(df)[0].eq(1).to_numpy()
        finisher_groups = [rows[~dnf[rows]] for rows in races]
        usable = [rows for rows in finisher_groups if len(rows) > 1]
        fit_rows = np.concatenate(usable) if usable else np.array([], dtype=int)
        weights = recency_weights(df)
        ranker = XGBRanker(objective="rank:pairwise", n_estimators=self.n_estimators, max_depth=3,
                           learning_rate=0.05, min_child_weight=5, reg_lambda=5.0,
                           subsample=0.8, colsample_bytree=0.8, n_jobs=1, random_state=self.random_seed)
        if len(fit_rows):
            relevance = np.concatenate([
                len(rows) - np.argsort(np.argsort(df.iloc[rows].target_position.to_numpy()))
                for rows in usable])
            ranker.fit(x[fit_rows], relevance, group=[len(rows) for rows in usable],
                       sample_weight=[weights[rows[0]] for rows in usable])
        else:
            ranker = None
            logger.warning("No races with two eligible finishers; ranking strengths remain neutral.")
        if not include_logistic:
            return imputer, ranker, [], None
        lean = [c for c in LOGIT_FEATURES if c in self.feature_columns_]
        if not lean:
            lean = self.feature_columns_[:8]
        logistic = make_pipeline(NeutralRaceImputer(neutral_position),
                                 StandardScaler(), LogisticRegression(C=0.1, max_iter=1000, random_state=self.random_seed))
        conditional_winner = np.zeros(len(df), dtype=int)
        for rows in usable:
            conditional_winner[rows[df.iloc[rows].target_position.to_numpy().argmin()]] = 1
        if len(fit_rows) and len(np.unique(conditional_winner[fit_rows])) > 1:
            logistic.fit(features.iloc[fit_rows][lean], conditional_winner[fit_rows],
                         logisticregression__sample_weight=weights[fit_rows])
        else:
            logistic = None
        return imputer, ranker, lean, logistic

    def _scores(self, df, components):
        imputer, ranker, lean, logistic = components
        x = df.reindex(columns=self.feature_columns_).replace([np.inf, -np.inf], np.nan)
        logit_scores = logistic.decision_function(x[lean]) if logistic is not None else np.zeros(len(df))
        rank_scores = ranker.predict(imputer.transform(x)) if ranker is not None else np.zeros(len(df))
        return np.column_stack([rank_scores, logit_scores])

    def _strength_distribution(self, df, components):
        if self.strength_model == "ensemble":
            from src.models.strength_ensemble import blend_distribution

            scores, covariance = self._ensemble_scores(df, components)
            return blend_distribution(scores, covariance, self.blend_weights_[0])
        return components.posterior(df)

    def _ensemble_scores(self, df, components):
        mean, covariance = components.bayesian.posterior(df)
        tree_scores = self._scores(df, components.xgb)[:, 0]
        tree_scores = (tree_scores - tree_scores.mean()) / components.xgb_scale
        scores = np.column_stack([mean / components.bayesian_scale, tree_scores])
        return scores, covariance / components.bayesian_scale**2

    def fit(self, df):
        if df.empty or "target_position" not in df:
            raise ValueError("Training requires nonempty races with target_position")
        races = chronological_races(df)
        driver_key = next((c for c in ["Abbreviation", "DriverNumber"] if c in df), None)
        ordered = pd.concat([df.iloc[rows].sort_values(driver_key) if driver_key else df.iloc[rows]
                             for rows in races], ignore_index=True)
        races = chronological_races(ordered)
        if "SessionStartUtc" in ordered:
            ordered["SessionStartUtc"] = pd.to_datetime(ordered.SessionStartUtc, utc=True)
        ordered["target_position"] = pd.to_numeric(ordered.target_position, errors="coerce")
        for rows in races:
            positions = pd.to_numeric(ordered.iloc[rows].target_position, errors="coerce").to_numpy()
            if not np.array_equal(np.sort(positions), np.arange(1, len(rows) + 1)):
                raise ValueError("Each race must contain the complete unique finishing positions 1..N")
            if "Abbreviation" in ordered and ordered.iloc[rows].Abbreviation.duplicated().any():
                raise ValueError("Duplicate driver in race")
        self.feature_columns_ = [c for c in get_feature_columns(ordered)
                                 if not any(key in c.lower() for key in ["timestamp", "_utc", "provenance", "prediction_", "model_version"])
                                 and c not in {"ClassifiedPosition", "Time", "Laps", "FastestLap", "IsPersonalBest"}]
        if not self.feature_columns_:
            raise ValueError("No numeric pre-race features")
        oof_scores, oof_frames, oof_risks, oof_correlations, folds = [], [], [], [], []
        oof_covariances = []
        first = max(2, len(races) // 3)
        for block in np.array_split(np.arange(first, len(races)), max(1, self.oof_splits)):
            if not len(block):
                continue
            train_rows = np.concatenate(races[:block[0]])
            train = ordered.iloc[train_rows].reset_index(drop=True)
            components = self._fit_strength(train, chronological_races(train))
            risk_model = ShrinkageDNFModel().fit(train)
            for i in block:
                valid = ordered.iloc[races[i]].reset_index(drop=True)
                if self.strength_model == "legacy":
                    oof_scores.append(self._scores(valid, components))
                    oof_covariances.append(None)
                elif self.strength_model == "ensemble":
                    scores, covariance = self._ensemble_scores(valid, components)
                    oof_scores.append(scores)
                    oof_covariances.append(covariance)
                else:
                    mean, covariance = self._strength_distribution(valid, components)
                    oof_scores.append(np.column_stack([mean, np.zeros(len(valid))]))
                    oof_covariances.append(covariance)
                oof_frames.append(valid)
                oof_risks.append(np.zeros(len(valid)) if "SessionType" in valid and
                                 valid.SessionType.isin(["Q", "SQ"]).all() else risk_model.predict(valid))
                oof_correlations.append(risk_model.race_correlation_)
            folds.append({"train_races": int(block[0]), "validation_races": block.tolist(),
                          "train_groups": race_groups(train).drop_duplicates().tolist(),
                          "validation_groups": [race_groups(ordered.iloc[races[i]]).iloc[0] for i in block],
                          "train_max_utc": str(train.SessionStartUtc.max()) if "SessionStartUtc" in train else None,
                          "validation_min_utc": str(ordered.iloc[races[block[0]]].SessionStartUtc.min()) if "SessionStartUtc" in ordered else None})
            if self.strength_model == "ensemble":
                folds[-1]["component_scales"] = {
                    "bayesian": components.bayesian_scale, "xgb": components.xgb_scale,
                    "source": "training_race_centered_rms",
                }
        conditional = [np.flatnonzero(~dnf_labels(frame)[0].eq(1).to_numpy()) for frame in oof_frames]
        self.blend_weights_ = np.array([0.5, 0.5] if self.strength_model == "legacy" else [1.0, 0.0])
        if oof_scores and self.strength_model == "legacy":
            def loss(weight):
                losses = []
                for scores, frame, rows in zip(oof_scores, oof_frames, conditional):
                    if len(rows) < 2:
                        continue
                    combined = scores[rows] @ np.array([weight, 1 - weight])
                    winner = int(frame.iloc[rows].target_position.to_numpy().argmin())
                    losses.append(logsumexp(combined) - combined[winner])
                return float(np.mean(losses)) if losses else 0.0
            weight = float(minimize_scalar(loss, bounds=(0, 1), method="bounded").x)
            self.blend_weights_ = np.array([weight, 1 - weight])
        if self.strength_model == "ensemble":
            from src.models.strength_ensemble import fit_ensemble_weight

            examples = [
                {"frame": frame, "component_scores": scores, "strength_covariance": covariance,
                 "p_dnf": risk, "race_correlation": correlation}
                for frame, scores, covariance, risk, correlation in
                zip(oof_frames, oof_scores, oof_covariances, oof_risks, oof_correlations)]
            weight = fit_ensemble_weight(examples, seed=self.random_seed)
            self.blend_weights_ = np.array([weight, 1 - weight])
        blended = [scores @ self.blend_weights_ for scores in oof_scores]
        self.temperature_ = 1.0
        self.calibration_examples_ = [
            {"frame": frame, "strength": strength, "p_dnf": risk, "race_correlation": correlation,
             "strength_covariance": (covariance * self.blend_weights_[0]**2
                                     if self.strength_model == "ensemble" else covariance)}
            for frame, strength, risk, correlation, covariance in
            zip(oof_frames, blended, oof_risks, oof_correlations, oof_covariances)]
        self.pl_temperature_ = fit_simulation_temperature(self.calibration_examples_, seed=self.random_seed)
        if len(blended) < 10:
            logger.warning("Only %s OOF races for calibration; probability calibration is weakly evidenced.", len(blended))
        self.components_ = self._fit_strength(ordered, races)
        self.dnf_model_ = ShrinkageDNFModel().fit(ordered)
        self.calibration_provenance_ = {"method": "expanding_race_oof", "n_races": len(blended),
                                        "folds": folds, "weights_source": "oof" if blended else "default",
                                        "temperature_source": "identity_single_final_scale",
                                        "pl_temperature_source": "oof_final_simulation" if blended else "default",
                                        "objective": "final_win_plus_podium_log_loss",
                                        "calibration_simulations": 1024, "seed": self.random_seed}
        self.metadata_ = {"model_version": MODEL_VERSIONS[self.strength_model],
                          "strength_model": self.strength_model,
                          "n_train_races": len(races), "n_train_rows": len(ordered),
                          "training_start": str(ordered.SessionStartUtc.min()) if "SessionStartUtc" in ordered else race_groups(ordered).iloc[0],
                          "training_end": str(ordered.SessionStartUtc.max()) if "SessionStartUtc" in ordered else race_groups(ordered).iloc[-1],
                          "temperature": self.temperature_, "pl_temperature": self.pl_temperature_,
                          "blend_weights": self.blend_weights_.tolist(), "calibration_provenance": self.calibration_provenance_,
                          "dnf_labels": {k: v["n_labels"] for k, v in self.dnf_model_.models_.items()},
                          "strength_target": "conditional_finisher_order",
                          "recency_policy": "30_event_half_life_prior_era_weight_0.25",
                          "uncertainty_kind": "conditional_outcomes_not_parameter_confidence",
                          "scenario_scope": "standard_dry_calibrated_other_scenarios_heuristic",
                          "retirement_dependence": "shrunk_race_gaussian_copula_moment_estimate",
                          "race_retirement_correlation": self.dnf_model_.race_correlation_,
                          "limitations": "constant_retirement_hazards; finishers_precede_retirees; no_fuel_load_data; no_parameter_posterior",
                          "dnf_fallback": "shrunk_total_risk_with_unknown_cause" if self.dnf_model_.total_fallback_ else
                          "beta_prior_for_missing_labels_and_unseen_entities"}
        if self.strength_model != "legacy":
            self.calibration_provenance_["weights_source"] = "fixed_single_bayesian_component"
            self.metadata_.update({
                "inference": "sequential_laplace_gaussian",
                "identification": "zero_sum_driver_and_constructor_contrasts",
                "recency_policy": "random_walk_14_day_units_constructor_reset_at_2022_and_2026",
                "uncertainty_kind": "laplace_strength_and_outcome_uncertainty",
                "limitations": "fixed_prior_and_process_scales; approximate_gaussian_posterior; "
                               "driver_constructor_not_causal; constant_retirement_hazards; finishers_precede_retirees",
            })
        if self.strength_model == "ensemble":
            self.calibration_provenance_.update({
                "weights_source": "oof_final_simulation" if blended else "fixed_equal_prior",
                "weight_objective": "final_win_plus_podium_log_loss_at_unit_temperature",
                "weight_simulations": 512,
                "score_scaling": "per_component_training_race_centered_rms",
            })
            self.metadata_["component_scales"] = {
                "bayesian": self.components_.bayesian_scale, "xgb": self.components_.xgb_scale}
            self.metadata_["component_order"] = ["bayesian", "xgb"]
            self.metadata_["limitations"] += "; ensemble_weight_and_xgb_parameter_uncertainty_not_integrated"
        return self

    def predict(self, df, n_simulations=20000, scenario="standard_dry", seed=42):
        if not hasattr(self, "components_"):
            raise ValueError("Fit RacePredictor before predicting")
        if df.empty:
            return df.copy()
        races = chronological_races(df)
        strength_model = getattr(self, "strength_model", "legacy")
        strengths = (self._scores(df, self.components_) @ self.blend_weights_ / self.temperature_
                     if strength_model == "legacy" else None)
        risks = self.dnf_model_.predict(df)
        components = self.dnf_model_.predict_components(df)
        if "SessionType" in df:
            qualifying = df.SessionType.isin(["Q", "SQ"]).to_numpy()
            risks[qualifying] = 0
            for values in components.values():
                values[qualifying] = 0
        outputs = []
        for rows in races:
            driver_key = next((c for c in ["Abbreviation", "DriverNumber"] if c in df), None)
            if driver_key:
                rows = rows[np.argsort(df.iloc[rows][driver_key].astype(str).to_numpy(), kind="stable")]
            race = df.iloc[rows].copy().reset_index(drop=True)
            if strengths is None:
                race_strength, covariance = self._strength_distribution(race, self.components_)
                posterior = {"strength_covariance": covariance}
            else:
                race_strength, covariance = strengths[rows], None
                posterior = {}
            # Simulator sorts by win chance; positional IDs restore the caller's row order.
            grid = race.copy()
            grid["Abbreviation"] = np.arange(len(race))
            simulated = MonteCarloRaceSimulator(n_simulations, seed).simulate(
                grid, session_scenario(race, scenario), strength=race_strength,
                p_dnf=risks[rows], pl_temperature=self.pl_temperature_,
                race_correlation=self.dnf_model_.race_correlation_, **posterior)
            simulated = simulated.set_index("Abbreviation").reindex(np.arange(len(race))).reset_index(drop=True)
            identifiers = [c for c in METADATA_COLUMNS if c in race]
            for c in ["GridPosition", "grid_position", "Driver", "FullName"]:
                if c in race and c not in identifiers:
                    identifiers.append(c)
            result = race[identifiers].copy()
            for col in simulated:
                if col not in {"Abbreviation", "TeamName"}:
                    result[col] = simulated[col].to_numpy()
            result["strength"] = race_strength
            if covariance is not None:
                result["strength_std"] = np.sqrt(np.maximum(np.diag(covariance), 0))
                result["strength_covariance_row"] = [json.dumps(row.tolist()) for row in covariance]
                result["strength_covariance_order"] = json.dumps(race[driver_key].astype(str).tolist())
                if "Abbreviation" not in result:
                    result["Abbreviation"] = race[driver_key].astype(str).to_numpy()
            result["base_dnf_probability"] = risks[rows]
            result["mechanical_dnf_probability"] = components["mechanical"][rows]
            result["collision_dnf_probability"] = components["collision"][rows]
            result["other_dnf_probability"] = components["other"][rows]
            result["scenario_kind"] = ("calibrated_reference" if scenario == "standard_dry"
                                       else "heuristic_sensitivity")
            result["model_version"] = MODEL_VERSIONS[strength_model]
            result["strength_model"] = strength_model
            result["training_start"] = self.metadata_["training_start"]
            result["training_end"] = self.metadata_["training_end"]
            result["n_train_races"] = self.metadata_["n_train_races"]
            if "stage" not in result:
                result["stage"] = "pre_weekend"
            result["_row"] = rows
            outputs.append(result)
        result = pd.concat(outputs, ignore_index=True).sort_values("_row").drop(columns="_row")
        # Variable field sizes have zero mass at non-existent finishing positions.
        for col in result.filter(regex=r"^prob_p\d+$"):
            result[col] = result[col].fillna(0)
        result.index = df.index
        return result


def train_and_save_race_model(feature_path, output_dir, strength_model="legacy"):
    """Persist a joblib dict with model, feature_columns, metadata; no evaluation writes."""
    frame = pd.read_parquet(feature_path)
    model = RacePredictor(strength_model=strength_model).fit(frame)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "race_model.joblib"
    artifact = {"model": model, "feature_columns": model.feature_columns_, "metadata": model.metadata_}
    joblib.dump(artifact, path)
    return {"model_path": path, "feature_columns": model.feature_columns_, "metadata": model.metadata_}
