"""One strength model and retirement model feeding coherent race simulations."""

from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar
from scipy.special import expit, logit, logsumexp
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from xgboost import XGBRanker

from src.models.calibrate import fit_pl_temperature, fit_temperature
from src.simulation.race_simulator import MonteCarloRaceSimulator
from src.utils.config import METADATA_COLUMNS, get_feature_columns

MODEL_VERSION = "race_pl_v1"
LOGIT_FEATURES = ["grid_position", "quali_position", "quali_gap_to_pole_pct",
                  "driver_race_form_5", "driver_race_season_avg_position",
                  "driver_race_season_points", "constructor_race_season_points",
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


def dnf_labels(df):
    """Missing labels remain unknown; never turn unobserved retirements into finishers."""
    status = df.get("Status", pd.Series(pd.NA, index=df.index)).astype("string").str.lower()
    collision_status = status.str.contains("collision|accident|spun", na=False)
    finished = status.str.contains(r"finished|\+\d+ lap", na=False)
    unknown_status = status.isna() | status.isin(["", "unknown", "nan"])
    total = pd.to_numeric(df.get("target_dnf", pd.Series(np.nan, index=df.index)), errors="coerce").astype(float)
    derived = (~finished).astype(float).mask(unknown_status)
    total = total.fillna(derived)
    if "ClassifiedPosition" in df:
        retired_code = df.ClassifiedPosition.astype("string").str.upper().isin(["R", "W", "F", "N"])
        total = total.fillna(pd.Series(np.where(retired_code, 1.0, np.nan), index=df.index))
    collision = pd.to_numeric(df.get("target_dnf_collision", pd.Series(np.nan, index=df.index)), errors="coerce").astype(float)
    collision = collision.fillna(collision_status.astype(float).mask(unknown_status))
    mechanical = pd.to_numeric(df.get("target_dnf_mechanical", pd.Series(np.nan, index=df.index)), errors="coerce").astype(float)
    mechanical = mechanical.fillna((total - collision).clip(0, 1))
    mechanical = mechanical.fillna(total.where(total.eq(0)))
    collision = collision.fillna(total.where(total.eq(0)))
    total = total.fillna(1 - (1 - mechanical) * (1 - collision))
    return total, mechanical, collision


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
        self.models_ = {}
        self.context_columns_ = [c for c in get_feature_columns(df)
                                 if any(key in c.lower() for key in ["rain", "grid_position", "circuit", "temperature"])][:8]
        for kind, labels, default in [("mechanical", mechanical, 0.035), ("collision", collision, 0.025)]:
            valid = labels.notna()
            prior = float((labels[valid].sum() + 20 * default) / (valid.sum() + 20))
            entities = self._entities(df, kind)
            observed = pd.DataFrame({"entity": entities.to_numpy(), "label": labels.to_numpy()}).dropna()
            stats = observed.groupby("entity").label.agg(["sum", "count"])
            rates = ((stats["sum"] + self.prior_starts * prior) / (stats["count"] + self.prior_starts)).to_dict()
            context = None
            if self.context_columns_ and valid.sum() >= 40 and labels[valid].sum() >= 5 and labels[valid].nunique() == 2:
                context = make_pipeline(NeutralRaceImputer(),
                                        StandardScaler(), LogisticRegression(C=0.05, max_iter=500))
                context.fit(df.loc[valid, self.context_columns_].replace([np.inf, -np.inf], np.nan), labels[valid])
            self.models_[kind] = {"prior": prior, "rates": rates, "context": context,
                                  "context_prior": float(labels[valid].mean()) if valid.any() else prior,
                                  "n_labels": int(valid.sum())}
        self.n_total_labels_ = int(total.notna().sum())
        self.total_fallback_ = None
        if self.n_total_labels_ and not mechanical.eq(1).any() and not collision.eq(1).any() and total.eq(1).any():
            # Unknown failure causes are not false mechanical/collision labels.
            prior = float((total.sum() + 20 * .06) / (self.n_total_labels_ + 20))
            rates = {}
            for kind in ["mechanical", "collision"]:
                observed = pd.DataFrame({"entity": self._entities(df, kind).to_numpy(), "label": total.to_numpy()}).dropna()
                stats = observed.groupby("entity").label.agg(["sum", "count"])
                rates[kind] = ((stats["sum"] + self.prior_starts * prior) / (stats["count"] + self.prior_starts)).to_dict()
            self.total_fallback_ = {"prior": prior, "rates": rates}
        return self

    def predict_components(self, df):
        if self.total_fallback_ is not None:
            fallback = self.total_fallback_
            total = np.mean([self._entities(df, kind).map(fallback["rates"][kind]).fillna(fallback["prior"]).to_numpy(float)
                             for kind in ["mechanical", "collision"]], axis=0)
            return {"mechanical": 1 - (1 - total) ** .6, "collision": 1 - (1 - total) ** .4}
        result = {}
        for kind, model in self.models_.items():
            rate = self._entities(df, kind).map(model["rates"]).fillna(model["prior"]).to_numpy(float)
            if model["context"] is not None:
                context_p = model["context"].predict_proba(
                    df.reindex(columns=self.context_columns_).replace([np.inf, -np.inf], np.nan))[:, 1]
                offset = logit(np.clip(context_p, 1e-5, 1 - 1e-5)) - logit(np.clip(model["context_prior"], 1e-5, 1 - 1e-5))
                rate = expit(logit(np.clip(rate, 1e-5, 1 - 1e-5)) + 0.25 * offset)
            result[kind] = rate
        return result

    def predict(self, df):
        risks = self.predict_components(df)
        return 1 - (1 - risks["mechanical"]) * (1 - risks["collision"])


class RacePredictor:
    """Regularized ranker/logit blend; all calibrators see forward-held-out scores only."""

    def __init__(self, random_seed=42, n_estimators=100, oof_splits=4):
        self.random_seed = random_seed
        self.n_estimators = n_estimators
        self.oof_splits = oof_splits

    def _fit_strength(self, df, races):
        neutral_position = float(np.median([len(rows) + 1 for rows in races]) / 2)
        imputer = NeutralRaceImputer(neutral_position)
        features = df.reindex(columns=self.feature_columns_).replace([np.inf, -np.inf], np.nan)
        x = imputer.fit_transform(features)
        dnf = dnf_labels(df)[0].fillna(0).to_numpy(bool)
        relevance = np.empty(len(df), dtype=int)
        for rows in races:
            relevance[rows] = np.where(dnf[rows], 0, len(rows) - df.target_position.to_numpy()[rows] + 1)
        ranker = XGBRanker(objective="rank:pairwise", n_estimators=self.n_estimators, max_depth=3,
                           learning_rate=0.05, min_child_weight=5, reg_lambda=5.0,
                           subsample=0.8, colsample_bytree=0.8, n_jobs=1, random_state=self.random_seed)
        ranker.fit(x, relevance, group=[len(rows) for rows in races])
        lean = [c for c in LOGIT_FEATURES if c in self.feature_columns_]
        if not lean:
            lean = self.feature_columns_[:8]
        logistic = make_pipeline(NeutralRaceImputer(neutral_position),
                                 StandardScaler(), LogisticRegression(C=0.1, max_iter=1000, random_state=self.random_seed))
        if df.target_position.eq(1).nunique() > 1:
            logistic.fit(features[lean], df.target_position.eq(1).astype(int))
        else:
            logistic = None
        return imputer, ranker, lean, logistic

    def _scores(self, df, components):
        imputer, ranker, lean, logistic = components
        x = df.reindex(columns=self.feature_columns_).replace([np.inf, -np.inf], np.nan)
        logit_scores = logistic.decision_function(x[lean]) if logistic is not None else np.zeros(len(df))
        return np.column_stack([ranker.predict(imputer.transform(x)), logit_scores])

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
        oof_scores, oof_frames, folds = [], [], []
        first = max(2, len(races) // 3)
        for block in np.array_split(np.arange(first, len(races)), max(1, self.oof_splits)):
            if not len(block):
                continue
            train_rows = np.concatenate(races[:block[0]])
            train = ordered.iloc[train_rows].reset_index(drop=True)
            components = self._fit_strength(train, chronological_races(train))
            for i in block:
                valid = ordered.iloc[races[i]].reset_index(drop=True)
                oof_scores.append(self._scores(valid, components))
                oof_frames.append(valid)
            folds.append({"train_races": int(block[0]), "validation_races": block.tolist(),
                          "train_groups": race_groups(train).drop_duplicates().tolist(),
                          "validation_groups": [race_groups(ordered.iloc[races[i]]).iloc[0] for i in block],
                          "train_max_utc": str(train.SessionStartUtc.max()) if "SessionStartUtc" in train else None,
                          "validation_min_utc": str(ordered.iloc[races[block[0]]].SessionStartUtc.min()) if "SessionStartUtc" in ordered else None})
        winners = [int(frame.target_position.to_numpy().argmin()) for frame in oof_frames]
        self.blend_weights_ = np.array([0.5, 0.5])
        if oof_scores:
            def loss(weight):
                return np.mean([logsumexp(scores @ np.array([weight, 1 - weight]))
                                - (scores @ np.array([weight, 1 - weight]))[winner]
                                for scores, winner in zip(oof_scores, winners)])
            weight = float(minimize_scalar(loss, bounds=(0, 1), method="bounded").x)
            self.blend_weights_ = np.array([weight, 1 - weight])
        blended = [scores @ self.blend_weights_ for scores in oof_scores]
        self.temperature_ = fit_temperature(blended, winners)
        orders = []
        for frame in oof_frames:
            finishers = np.flatnonzero(~dnf_labels(frame)[0].fillna(0).to_numpy(bool))
            orders.append(finishers[np.argsort(frame.target_position.to_numpy()[finishers])])
        # PL temperature is fitted after win-temperature scaling, not multiplied twice.
        self.pl_temperature_ = fit_pl_temperature([s / self.temperature_ for s in blended], orders)
        self.components_ = self._fit_strength(ordered, races)
        self.dnf_model_ = ShrinkageDNFModel().fit(ordered)
        self.calibration_provenance_ = {"method": "expanding_race_oof", "n_races": len(blended),
                                        "folds": folds, "weights_source": "oof" if blended else "default",
                                        "temperature_source": "oof" if blended else "default",
                                        "pl_temperature_source": "oof_finishers" if any(len(o) > 1 for o in orders) else "default"}
        self.metadata_ = {"model_version": MODEL_VERSION, "n_train_races": len(races), "n_train_rows": len(ordered),
                          "training_start": str(ordered.SessionStartUtc.min()) if "SessionStartUtc" in ordered else race_groups(ordered).iloc[0],
                          "training_end": str(ordered.SessionStartUtc.max()) if "SessionStartUtc" in ordered else race_groups(ordered).iloc[-1],
                          "temperature": self.temperature_, "pl_temperature": self.pl_temperature_,
                          "blend_weights": self.blend_weights_.tolist(), "calibration_provenance": self.calibration_provenance_,
                          "dnf_labels": {k: v["n_labels"] for k, v in self.dnf_model_.models_.items()},
                          "dnf_fallback": "shrunk_total_risk_with_prior_cause_split" if self.dnf_model_.total_fallback_ else
                          "beta_prior_for_missing_labels_and_unseen_entities"}
        return self

    def predict(self, df, n_simulations=20000, scenario="standard_dry", seed=42):
        if not hasattr(self, "components_"):
            raise ValueError("Fit RacePredictor before predicting")
        if df.empty:
            return df.copy()
        races = chronological_races(df)
        strengths = self._scores(df, self.components_) @ self.blend_weights_ / self.temperature_
        risks = self.dnf_model_.predict(df)
        components = self.dnf_model_.predict_components(df)
        outputs = []
        for rows in races:
            driver_key = next((c for c in ["Abbreviation", "DriverNumber"] if c in df), None)
            if driver_key:
                rows = rows[np.argsort(df.iloc[rows][driver_key].astype(str).to_numpy(), kind="stable")]
            race = df.iloc[rows].copy().reset_index(drop=True)
            # Simulator sorts by win chance; positional IDs restore the caller's row order.
            grid = race.copy()
            grid["Abbreviation"] = np.arange(len(race))
            simulated = MonteCarloRaceSimulator(n_simulations, seed).simulate(
                grid, scenario, strength=strengths[rows], p_dnf=risks[rows], pl_temperature=self.pl_temperature_)
            simulated = simulated.set_index("Abbreviation").reindex(np.arange(len(race))).reset_index(drop=True)
            identifiers = [c for c in METADATA_COLUMNS if c in race]
            for c in ["GridPosition", "Driver", "FullName"]:
                if c in race and c not in identifiers:
                    identifiers.append(c)
            result = race[identifiers].copy()
            for col in simulated:
                if col not in {"Abbreviation", "TeamName"}:
                    result[col] = simulated[col].to_numpy()
            result["strength"] = strengths[rows]
            result["mechanical_dnf_probability"] = components["mechanical"][rows]
            result["collision_dnf_probability"] = components["collision"][rows]
            result["model_version"] = MODEL_VERSION
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


def train_and_save_race_model(feature_path, output_dir):
    """Persist a joblib dict with model, feature_columns, metadata; no evaluation writes."""
    frame = pd.read_parquet(feature_path)
    model = RacePredictor().fit(frame)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "race_model.joblib"
    artifact = {"model": model, "feature_columns": model.feature_columns_, "metadata": model.metadata_}
    joblib.dump(artifact, path)
    return {"model_path": path, "feature_columns": model.feature_columns_, "metadata": model.metadata_}
