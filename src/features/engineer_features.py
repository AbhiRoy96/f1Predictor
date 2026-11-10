from pathlib import Path
import re
import numpy as np
import pandas as pd

from src.data.normalise_entities import (
    build_driver_lookup, constructor_lineage, normalise_circuit,
    normalise_constructor, normalise_driver_name,
)
from src.data.outcomes import dnf_labels
from src.utils.config import get_feature_columns

RAW = Path("data/raw")
OUTPUT = Path("data/processed")
TARGET_TYPES = {"Q", "SQ", "S", "R"}
RACE_TYPES = {"R", "S"}
QUALIFYING_TYPES = {"Q", "SQ"}
SESSION_RANK = {"FP1": 1, "FP2": 2, "FP3": 3, "SQ": 4, "S": 5, "Q": 6, "R": 7}
SESSION_ALIASES = {
    "race": "R",
    "qualifying": "Q",
    "sprint": "S",
    "sprint qualifying": "SQ",
    "sprint shootout": "SQ",
    "practice 1": "FP1",
    "practice 2": "FP2",
    "practice 3": "FP3",
}
WEATHER_COLUMNS = ("AirTemp", "TrackTemp", "Humidity", "Pressure", "WindSpeed", "Rainfall")
STAGES = ("pre_weekend", "post_fp", "post_quali")
NEUTRAL_POSITION = 10.5


def _session_code(value):
    if pd.isna(value):
        return "R"
    value = str(value).strip()
    return SESSION_ALIASES.get(value.casefold(), value.upper())


def _session_type_from_filename(path):
    stem = Path(path).stem.casefold()
    match = re.search(r"_(fp[123]|sq|q|s|r)_results$", stem)
    if match:
        return match.group(1).upper()
    if "sprint_qualifying" in stem or "sprint_shootout" in stem:
        return "SQ"
    if "qualifying" in stem:
        return "Q"
    if "sprint" in stem:
        return "S"
    if "race" in stem:
        return "R"
    return "R"


def _read_parquets(pattern):
    files = sorted(RAW.glob(pattern))
    frames = [pd.read_parquet(path) for path in files]
    return pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()


def load_results():
    files = sorted(RAW.glob("*_results.parquet"))
    if not files:
        raise FileNotFoundError("No session result parquet files found")

    frames = []
    for path in files:
        frame = pd.read_parquet(path)
        if "SessionType" not in frame.columns:
            frame["SessionType"] = _session_type_from_filename(path)
        frames.append(frame)
    return _normalize_results(pd.concat(frames, ignore_index=True, sort=False))


def _schedule_start_lookup(results):
    lookup = {}
    for path in RAW.glob("*_schedule.parquet"):
        schedule = pd.read_parquet(path)
        year_from_name = re.match(r"(\d{4})_", path.name)
        fallback_year = int(year_from_name.group(1)) if year_from_name else None
        for _, event in schedule.iterrows():
            year = event.get("Year", fallback_year)
            round_number = event.get("RoundNumber")
            if pd.isna(year) or pd.isna(round_number):
                continue
            for number in range(1, 6):
                session_type = _session_code(event.get(f"Session{number}"))
                start = pd.to_datetime(event.get(f"Session{number}DateUtc"), utc=True, errors="coerce")
                if pd.isna(start):
                    start = pd.to_datetime(event.get(f"Session{number}Date"), utc=True, errors="coerce")
                if session_type != "R" or not pd.isna(start):
                    lookup[(int(year), int(round_number), session_type)] = start
    return lookup


def _normalize_results(frame):
    frame = frame.copy()
    if "Year" not in frame:
        raise ValueError("Missing required column: Year")
    if "RoundNumber" not in frame:
        frame["RoundNumber"] = 0
    if "Abbreviation" not in frame:
        raise ValueError("Missing required column: Abbreviation")
    if "TeamName" not in frame:
        frame["TeamName"] = np.nan
    if "SessionType" not in frame:
        frame["SessionType"] = "R"
    for column, default in (("Position", np.nan), ("Points", np.nan), ("GridPosition", np.nan)):
        if column not in frame:
            frame[column] = default

    frame["Year"] = pd.to_numeric(frame["Year"], errors="coerce")
    frame["RoundNumber"] = pd.to_numeric(frame["RoundNumber"], errors="coerce").fillna(0)
    frame["SessionType"] = frame["SessionType"].map(_session_code)
    frame["Position"] = pd.to_numeric(frame["Position"], errors="coerce")
    frame["Points"] = pd.to_numeric(frame["Points"], errors="coerce")
    frame["GridPosition"] = pd.to_numeric(frame["GridPosition"], errors="coerce")
    frame["SessionStartUtc"] = pd.to_datetime(frame.get("SessionStartUtc"), utc=True, errors="coerce")
    if "CircuitName" not in frame:
        if "Location" in frame:
            frame["CircuitName"] = frame["Location"]
        elif "EventName" in frame:
            frame["CircuitName"] = frame["EventName"]
        else:
            frame["CircuitName"] = np.nan
    if "EventName" not in frame:
        frame["EventName"] = frame["CircuitName"]

    schedule_lookup = _schedule_start_lookup(frame)
    for index in frame.index[frame["SessionStartUtc"].isna()]:
        row = frame.loc[index]
        key = (int(row["Year"]), int(row["RoundNumber"]), row["SessionType"])
        frame.at[index, "SessionStartUtc"] = schedule_lookup.get(key, pd.NaT)

    # Never invent timestamps: unknown session provenance cannot be a predictor.
    frame["TeamName"] = frame["TeamName"].map(normalise_constructor)
    frame["team_lineage"] = frame["TeamName"].map(constructor_lineage)
    frame["CircuitName"] = frame["CircuitName"].map(normalise_circuit)
    frame["Position"] = frame["Position"].where(frame["Position"].ge(1))
    frame["_dnf"], frame["_mechanical"], frame["_collision"] = _dnf_labels(frame)

    frame["_race_outcome"] = frame["SessionType"].isin(RACE_TYPES)
    frame["_winner"] = (frame["_race_outcome"] & frame["Position"].eq(1)).astype(int)
    frame["_podium"] = (frame["_race_outcome"] & frame["Position"].between(1, 3)).astype(int)
    frame["_category"] = np.select(
        [frame["SessionType"].eq(code) for code in ("R", "S", "Q", "SQ")],
        ["race", "sprint", "qualifying", "sprint_qualifying"],
        default="other",
    )
    keys = ["Year", "RoundNumber", "SessionType", "Abbreviation"]
    return frame.drop_duplicates(subset=keys, keep="last").sort_values(
        ["SessionStartUtc", "Year", "RoundNumber", "SessionType", "Abbreviation"]
    ).reset_index(drop=True)


def _dnf_labels(frame):
    race = frame["SessionType"].isin(RACE_TYPES)
    return tuple(values.where(race) for values in dnf_labels(frame))


def _prepare_auxiliary(frame, results):
    if frame is None or frame.empty:
        return pd.DataFrame()
    frame = frame.copy()
    for column in ("Year", "RoundNumber"):
        if column not in frame:
            frame[column] = np.nan
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if "SessionStartUtc" not in frame:
        frame["SessionStartUtc"] = pd.NaT
    frame["SessionStartUtc"] = pd.to_datetime(frame["SessionStartUtc"], utc=True, errors="coerce")
    if "SessionType" in frame:
        frame["SessionType"] = frame["SessionType"].map(_session_code)
        frame = frame.loc[frame["SessionType"].isin(SESSION_RANK)]
        keys = ["Year", "RoundNumber", "SessionType"]
        lookup = results.dropna(subset=["SessionStartUtc"]).drop_duplicates(keys)[keys + ["SessionStartUtc"]]
        frame = frame.merge(lookup.rename(columns={"SessionStartUtc": "_canonical_start"}),
                            on=keys, how="left")
        mismatch = (frame["SessionStartUtc"].notna() & frame["_canonical_start"].notna()
                    & frame["SessionStartUtc"].ne(frame["_canonical_start"]))
        frame = frame.loc[~mismatch].copy()
        frame["SessionStartUtc"] = frame["SessionStartUtc"].fillna(frame["_canonical_start"])
        frame = frame.drop(columns="_canonical_start")
    # Session type is mandatory even if a timestamp happens to be present.
    if "SessionType" not in frame:
        return pd.DataFrame()
    frame = frame.dropna(subset=["SessionStartUtc", "Year", "RoundNumber"])
    if "Abbreviation" not in frame and "Driver" in frame:
        frame["Abbreviation"] = frame["Driver"]
    if "Abbreviation" not in frame and "DriverNumber" in frame and "DriverNumber" in results:
        mapping = results.dropna(subset=["DriverNumber"]).assign(
            DriverNumber=lambda data: data["DriverNumber"].astype(str)
        ).drop_duplicates(["Year", "RoundNumber", "DriverNumber"])
        frame["DriverNumber"] = frame["DriverNumber"].astype(str)
        frame = frame.merge(mapping[["Year", "RoundNumber", "DriverNumber", "Abbreviation"]],
                            on=["Year", "RoundNumber", "DriverNumber"], how="left")
    return frame.sort_values("SessionStartUtc")


def _prior_weekend_rows(frame, target):
    if frame.empty or "SessionStartUtc" not in frame:
        return pd.DataFrame()
    same_event = frame["Year"].eq(target["Year"]) & frame["RoundNumber"].eq(target["RoundNumber"])
    earlier = frame["SessionStartUtc"].notna() & frame["SessionStartUtc"].lt(target["FeatureCutoffUtc"])
    allowed = frame["SessionType"].ne(target["SessionType"])
    if target["stage"] == "pre_weekend":
        allowed &= False
    elif target["stage"] == "post_fp":
        allowed &= frame["SessionType"].isin(["FP1", "FP2", "FP3"])
    # Explicit observation times must precede the cutoff too.
    for column in ("Date", "Timestamp", "PublishedAt", "LapStartDate"):
        if column in frame:
            observed = pd.to_datetime(frame[column], utc=True, errors="coerce")
            earlier &= observed.isna() | observed.lt(target["FeatureCutoffUtc"])
    if "Time" in frame and pd.api.types.is_timedelta64_dtype(frame["Time"]):
        observed = frame["SessionStartUtc"] + frame["Time"]
        earlier &= observed.isna() | observed.lt(target["FeatureCutoffUtc"])
    if "SessionEndUtc" in frame:
        end = pd.to_datetime(frame["SessionEndUtc"], utc=True, errors="coerce")
        earlier &= end.isna() | end.lt(target["FeatureCutoffUtc"])
    return frame[same_event & earlier & allowed].copy()


def _seconds(values):
    if pd.api.types.is_numeric_dtype(values):
        return pd.to_numeric(values, errors="coerce")
    return pd.to_timedelta(values, errors="coerce").dt.total_seconds()


def _clean_pace_laps(laps):
    """Keep comparable clean laps and identify within-session track-time cohorts."""
    frame = laps.copy()
    if "LapTime" not in frame:
        return frame.iloc[:0]
    frame["seconds"] = _seconds(frame["LapTime"])
    frame = frame.loc[frame.seconds.gt(0)]
    if "IsAccurate" in frame:
        frame = frame.loc[frame.IsAccurate.fillna(False).astype(bool)]
    if "Deleted" in frame:
        frame = frame.loc[~frame.Deleted.fillna(False).astype(bool)]
    if "TrackStatus" in frame:
        frame = frame.loc[frame.TrackStatus.astype(str).isin(["1", "1.0"])]
    for column in ("PitInTime", "PitOutTime"):
        if column in frame:
            frame = frame.loc[frame[column].isna()]
    if "LapStartDate" in frame:
        observed = pd.to_datetime(frame.LapStartDate, utc=True, errors="coerce")
        elapsed = (observed - frame.SessionStartUtc).dt.total_seconds()
    elif "Time" in frame:
        elapsed = _seconds(frame.Time)
    else:
        elapsed = pd.Series(np.nan, index=frame.index)
    frame["_pace_time_known"] = elapsed.notna()
    frame["_pace_time_bin"] = (elapsed / 600).floordiv(1).fillna(-1)
    return frame


def _weekend_features(target, abbreviation, laps, weather, messages, results):
    features = {}
    prior_laps = _prior_weekend_rows(laps, target)
    if not prior_laps.empty:
        if "Abbreviation" not in prior_laps:
            if "Driver" in prior_laps:
                prior_laps["Abbreviation"] = prior_laps["Driver"]
            elif "DriverNumber" in prior_laps:
                driver_map = results.dropna(subset=["DriverNumber"]).drop_duplicates(
                    ["Year", "RoundNumber", "DriverNumber"]
                ).set_index(["Year", "RoundNumber", "DriverNumber"])["Abbreviation"]
                prior_laps["Abbreviation"] = [
                    driver_map.get((row.Year, row.RoundNumber, row.DriverNumber), np.nan)
                    for row in prior_laps.itertuples()
                ]
        driver_laps = (prior_laps[prior_laps["Abbreviation"].astype(str).eq(str(abbreviation))]
                       if "Abbreviation" in prior_laps else pd.DataFrame())
        lap_seconds = _seconds(driver_laps["LapTime"]) if "LapTime" in driver_laps else pd.Series(dtype=float)
        features["prior_weekend_lap_count"] = len(driver_laps)
        features["prior_weekend_best_lap_seconds"] = lap_seconds.min()
        features["prior_weekend_mean_lap_seconds"] = lap_seconds.mean()
        features["prior_weekend_mean_tyre_life"] = (
            pd.to_numeric(driver_laps["TyreLife"], errors="coerce").mean()
            if "TyreLife" in driver_laps else np.nan
        )
        features["prior_weekend_compound_count"] = (
            driver_laps["Compound"].nunique(dropna=True) if "Compound" in driver_laps else np.nan
        )
        features["prior_weekend_last_compound"] = (
            driver_laps["Compound"].dropna().astype(str).str.upper().map(
                {"SOFT": 1, "MEDIUM": 2, "HARD": 3, "INTERMEDIATE": 4, "WET": 5}
            ).iloc[-1]
            if "Compound" in driver_laps and driver_laps["Compound"].notna().any() else np.nan
        )
    else:
        for name in (
            "prior_weekend_lap_count", "prior_weekend_best_lap_seconds",
            "prior_weekend_mean_lap_seconds", "prior_weekend_mean_tyre_life",
            "prior_weekend_compound_count", "prior_weekend_last_compound",
        ):
            features[name] = np.nan

    prior_weather = _prior_weekend_rows(weather, target)
    for column in WEATHER_COLUMNS:
        if column in prior_weather:
            values = pd.to_numeric(prior_weather[column], errors="coerce")
            features[f"prior_weekend_{column.casefold()}_mean"] = values.mean()
            features[f"prior_weekend_{column.casefold()}_last"] = values.iloc[-1] if values.notna().any() else np.nan
        else:
            features[f"prior_weekend_{column.casefold()}_mean"] = np.nan
            features[f"prior_weekend_{column.casefold()}_last"] = np.nan

    prior_messages = _prior_weekend_rows(messages, target)
    if prior_messages.empty:
        features["prior_weekend_message_count"] = np.nan
        for term in ("safety_car", "virtual_safety_car", "yellow_flag", "red_flag"):
            features[f"prior_weekend_{term}_messages"] = np.nan
    else:
        text_columns = [column for column in ("Message", "Category", "Flag") if column in prior_messages]
        if prior_messages.empty or not text_columns:
            text = pd.Series("", index=prior_messages.index, dtype=str)
        else:
            text = (
                prior_messages[text_columns]
                .fillna("")
                .astype(str)
                .apply(lambda values: " ".join(values), axis=1)
                .str.casefold()
            )
        features["prior_weekend_message_count"] = len(prior_messages)
        for term, pattern in (
            ("safety_car", r"safety car"),
            ("virtual_safety_car", r"virtual safety car|\bvsc\b"),
            ("yellow_flag", r"yellow"),
            ("red_flag", r"red flag"),
        ):
            features[f"prior_weekend_{term}_messages"] = int(text.str.contains(pattern, regex=True).sum())
    return features


def _news_features(target, news):
    if news.empty:
        return {"prior_news_item_count": np.nan, "prior_news_tyre_mentions": np.nan, "prior_news_weather_mentions": np.nan}
    published_column = next((column for column in ("PublishedAt", "published_at", "PublishedUTC") if column in news), None)
    if published_column is None:
        return {"prior_news_item_count": np.nan, "prior_news_tyre_mentions": np.nan, "prior_news_weather_mentions": np.nan}
    published = pd.to_datetime(news[published_column], utc=True, errors="coerce")
    mask = published.notna() & published.lt(target["FeatureCutoffUtc"])
    if "Year" in news:
        mask &= pd.to_numeric(news["Year"], errors="coerce").eq(target["Year"])
    event_column = next((column for column in ("EventName", "CircuitName") if column in news), None)
    if event_column is None and "Year" not in news:
        return {"prior_news_item_count": np.nan, "prior_news_tyre_mentions": np.nan, "prior_news_weather_mentions": np.nan}
    if event_column:
        event_value = target.get(event_column)
        if pd.isna(event_value) and event_column == "CircuitName":
            event_value = target.get("EventName")
        mask &= news[event_column].astype(str).str.casefold().eq(str(event_value).casefold())
    selected = news.loc[mask]
    text_column = next((column for column in ("Text", "Headline", "Description") if column in selected), None)
    text = selected[text_column].fillna("").astype(str).str.casefold() if text_column else pd.Series("", index=selected.index)
    return {
        "news_source_max_utc": published.loc[mask].max(),
        "prior_news_item_count": len(selected),
        "prior_news_tyre_mentions": int(text.str.contains(r"tyre|tire", regex=True).sum()),
        "prior_news_weather_mentions": int(text.str.contains(r"weather|rain|wind|temperature", regex=True).sum()),
    }


class _HistoryIndex:
    """Prefix aggregates built once; each query uses three binary searches."""

    def __init__(self, results, team=False):
        eligible = results.loc[results["_category"].isin(["race", "sprint", "qualifying", "sprint_qualifying"])
                               & results["Position"].notna()
                               & results["SessionStartUtc"].notna()].copy()
        self.team = team
        self.entity = "team_lineage" if team else "Abbreviation"
        if team:
            eligible = eligible.groupby(
                ["team_lineage", "_category", "Year", "RoundNumber", "CircuitName", "SessionStartUtc"],
                as_index=False, dropna=False,
            ).agg(Position=("Position", "mean"), Points=("Points", "sum"),
                  _winner=("_winner", "sum"), _podium=("_podium", "sum"),
                  _dnf=("_dnf", "mean"), _mechanical=("_mechanical", "mean"),
                  _collision=("_collision", "mean"))
        eligible["_era"] = np.select([eligible.Year.ge(2026), eligible.Year.ge(2022)], [2026, 2022], default=2014)
        self.groups = {}
        for dimension in (None, "Year", "CircuitName", "_era"):
            keys = [self.entity, "_category"] + ([dimension] if dimension else [])
            for key, group in eligible.groupby(keys, dropna=False):
                group = group.sort_values("SessionStartUtc")
                stats = pd.DataFrame(index=group.index)
                stats["count"] = np.arange(1, len(group) + 1)
                for column in ("Points", "_winner", "_podium"):
                    stats[column] = group[column].fillna(0).cumsum()
                stats["position"] = group["Position"].expanding().mean()
                stats["form_3"] = group["Position"].rolling(3, min_periods=1).mean()
                stats["form_5"] = group["Position"].rolling(5, min_periods=1).mean()
                stats["points_5"] = group["Points"].rolling(5, min_periods=1).sum()
                stats["ewma_position"] = group["Position"].ewm(halflife=5).mean()
                stats["ewma_points"] = group["Points"].ewm(halflife=5).mean()
                if not team:
                    grid = group["GridPosition"].where(group["GridPosition"].gt(0))
                    stats["finish_vs_grid"] = (group["Position"] - grid).ewm(halflife=5).mean()
                    stats["position_change"] = (group["Position"] - grid).abs().expanding().mean()
                for column in ("_dnf", "_mechanical", "_collision"):
                    stats[column] = group[column].fillna(0).cumsum()
                    stats[column + "_n"] = group[column].notna().cumsum()
                self.groups[(dimension, key)] = (group["SessionStartUtc"].dt.as_unit("ns").astype("int64").to_numpy(),
                                                  stats.to_dict("records"))

    def _lookup(self, entity, category, cutoff, dimension=None, value=None):
        key = (entity, category) + ((value,) if dimension else ())
        indexed = self.groups.get((dimension, key))
        if indexed is None or pd.isna(cutoff):
            return {}, pd.NaT
        times, records = indexed
        index = times.searchsorted(cutoff.value, side="left") - 1
        if index < 0:
            return {}, pd.NaT
        return records[index], pd.Timestamp(times[index], tz="UTC")

    def features(self, target, category):
        entity = target[self.entity]
        cutoff = target["FeatureCutoffUtc"]
        prefix = ("constructor" if self.team else "driver") + "_" + category + "_"
        if target["SessionType"] in {"S", "SQ"}:
            category = {"race": "sprint", "qualifying": "sprint_qualifying"}.get(category, category)
        era = 2026 if target["Year"] >= 2026 else (2022 if target["Year"] >= 2022 else 2014)
        features = {}
        career, source = self._lookup(entity, category, cutoff)
        era_stats, _ = self._lookup(entity, category, cutoff, "_era", era)
        current = {}
        for name, dimension, value in (("career", None, None), ("season", "Year", target["Year"]),
                                       ("circuit", "CircuitName", target["CircuitName"]),
                                       ("era", "_era", era)):
            stats, _ = self._lookup(entity, category, cutoff, dimension, value)
            if name == "season":
                current = stats
            features[prefix + name + ("_sessions" if self.team else "_starts")] = stats.get("count", 0)
            for metric, column in (("wins", "_winner"), ("podiums", "_podium"), ("points", "Points")):
                features[prefix + name + "_" + metric] = stats.get(column, 0)
            features[prefix + name + "_avg_position"] = stats.get("position", np.nan)
            features[prefix + name + "_points_per_start"] = (
                stats.get("Points", 0) / stats["count"] if stats.get("count", 0) else np.nan)
        for metric in ("form_3", "form_5", "points_5", "ewma_position", "ewma_points"):
            features[prefix + metric] = era_stats.get(metric, np.nan if metric != "points_5" else 0)
        if not self.team:
            features[prefix + "ewma_finish_vs_grid"] = career.get("finish_vs_grid", np.nan)
            features[prefix + "position_change"] = career.get("position_change", np.nan)
        n = current.get("count", 0)
        weight = n / (n + 5)
        year_start = pd.Timestamp(year=int(target["Year"]), month=1, day=1, tz="UTC")
        previous, _ = self._lookup(entity, category, min(cutoff, year_start))
        prior = previous.get("position", NEUTRAL_POSITION)
        features[prefix + "strength_position"] = weight * current.get("position", prior) + (1 - weight) * prior
        era_previous, _ = self._lookup(entity, category, min(cutoff, year_start), "_era", era)
        era_prior = era_previous.get("position", NEUTRAL_POSITION)
        features[prefix + "era_strength_position"] = (
            weight * current.get("position", era_prior) + (1 - weight) * era_prior)
        features[prefix + "strength_current_weight"] = weight
        for metric, column, prior_rate in (("dnf", "_dnf", .05), ("mechanical", "_mechanical", .03),
                                           ("collision", "_collision", .02)):
            features[prefix + metric + "_rate_shrunk"] = (
                era_stats.get(column, 0) + 20 * prior_rate) / (era_stats.get(column + "_n", 0) + 20)
        return features, source


def _event_groups(frame):
    if frame.empty:
        return {}
    return dict(tuple(frame.groupby(["Year", "RoundNumber"], sort=False)))


def _stage_cutoff(target, event, stage):
    start = target["SessionStartUtc"]
    earlier = event.loc[event["SessionStartUtc"].le(start)]
    if stage == "pre_weekend":
        return earlier["SessionStartUtc"].min()
    if stage == "post_fp":
        non_practice = earlier.loc[~earlier["SessionType"].isin(["FP1", "FP2", "FP3"])]
        return non_practice["SessionStartUtc"].min()
    return start


def _neutral_positions(row):
    columns = set(get_feature_columns(pd.DataFrame([row])))
    for column, value in list(row.items()):
        if column in columns:
            if pd.isna(value):
                row[column + "_is_missing"] = 1
            else:
                row[column + "_is_missing"] = 0
            if ("avg_position" in column or "_form_" in column or column.endswith("ewma_position")
                    or column in ("grid_position", "quali_position", "fp2_long_run_pace_rank")):
                if pd.isna(value) or value <= 0:
                    row[column] = NEUTRAL_POSITION


def _stage_features(target, event, laps, weather, messages):
    prior = _prior_weekend_rows(event, target)
    selected = [_prior_weekend_rows(frame, target) for frame in (laps, weather, messages)]
    provenance = [frame for frame in [prior] + selected if not frame.empty]
    features = {
        "weekend_source_max_utc": max(frame["SessionStartUtc"].max() for frame in provenance) if provenance else pd.NaT,
        "weekend_source_session_types": ",".join(sorted(set().union(
            *(set(frame["SessionType"]) for frame in provenance)))) if provenance else "",
        "grid_source_max_utc": pd.NaT,
    }
    for column in ("grid_position", "quali_position", "quali_gap_to_pole_pct",
                   "quali_gap_to_teammate_pct", "grid_penalty_places", "pit_lane_start",
                   "fp2_long_run_pace_rank", "fp_best_lap_gap_pct", "fp_teammate_gap_pct"):
        features[column] = np.nan
    for column in ("quali_reference_phase", "fp2_long_run_pace_sample_count",
                   "fp2_long_run_pace_mad", "fp2_track_time_coverage"):
        features[column] = np.nan
    qualifying_type = "SQ" if target["SessionType"] == "S" else "Q"
    qualifying = prior.loc[prior["SessionType"].eq(qualifying_type)]
    driver = qualifying.loc[qualifying["Abbreviation"].eq(target["Abbreviation"])]
    if target["stage"] == "post_quali" and target["SessionType"] in RACE_TYPES:
        if not driver.empty:
            features["quali_position"] = driver.iloc[-1]["Position"]
            best = pd.DataFrame(index=qualifying.index)
            for column in ("Q1", "Q2", "Q3", "BestLapTime"):
                if column in qualifying:
                    best[column] = _seconds(qualifying[column])
            own_rows = best.reindex(driver.index)
            phases = [c for c in ("Q3", "Q2", "Q1", "BestLapTime")
                      if c in own_rows and own_rows[c].gt(0).any()]
            phase = phases[0] if phases else None
            times = (best[phase].where(lambda values: values.gt(0)) if phase else
                     pd.Series(np.nan, index=qualifying.index))
            features["quali_reference_phase"] = {"Q1": 1, "Q2": 2, "Q3": 3}.get(phase, 0)
            own_time = times.loc[driver.index].min()
            mates = qualifying.loc[qualifying["team_lineage"].eq(target["team_lineage"])
                                    & qualifying["Abbreviation"].ne(target["Abbreviation"])]
            pole, mate = times.min(), times.reindex(mates.index).min()
            if pd.notna(pole) and pd.notna(own_time):
                features["quali_gap_to_pole_pct"] = (own_time / pole - 1) * 100
            if pd.notna(mate) and pd.notna(own_time):
                features["quali_gap_to_teammate_pct"] = (own_time / mate - 1) * 100
            grid = driver.iloc[-1]["GridPosition"]
            if pd.notna(grid) and grid >= 0:
                features["grid_position"] = grid if grid > 0 else np.nan
                features["pit_lane_start"] = int(grid == 0)
                features["grid_source_max_utc"] = driver.iloc[-1]["SessionStartUtc"]
        published = pd.to_datetime(target.get("GridPublishedAt"), utc=True, errors="coerce")
        if pd.notna(published) and published < target["FeatureCutoffUtc"]:
            grid = target["GridPosition"]
            if pd.notna(grid) and grid >= 0:
                features["grid_position"] = grid if grid > 0 else np.nan
                features["pit_lane_start"] = int(grid == 0)
                features["grid_source_max_utc"] = published
        if pd.notna(features["grid_position"]) and pd.notna(features["quali_position"]):
            features["grid_penalty_places"] = features["quali_position"] - features["grid_position"]

    practice = selected[0]
    if not practice.empty and "LapTime" in practice and "Abbreviation" in practice:
        practice = _clean_pace_laps(practice.loc[practice["SessionType"].isin(["FP1", "FP2", "FP3"])])
        own_laps = practice.loc[practice.Abbreviation.eq(target["Abbreviation"])]
        comparison = practice.iloc[:0]
        if not own_laps.empty:
            own_session = own_laps.SessionStartUtc.max()
            comparison = practice.loc[practice.SessionStartUtc.eq(own_session)]
            own_latest = own_laps.loc[own_laps.SessionStartUtc.eq(own_session)]
            if "Compound" in comparison:
                compound = own_latest.loc[own_latest.seconds.idxmin(), "Compound"]
                comparison = comparison.loc[comparison.Compound.eq(compound)]
        best = comparison.groupby("Abbreviation")["seconds"].min()
        own = best.get(target["Abbreviation"], np.nan)
        if pd.notna(own) and not best.empty:
            features["fp_best_lap_gap_pct"] = (own / best.min() - 1) * 100
            mate_codes = prior.loc[prior["team_lineage"].eq(target["team_lineage"])
                                   & prior["Abbreviation"].ne(target["Abbreviation"]), "Abbreviation"]
            mate = best.reindex(mate_codes).min()
            if pd.notna(mate):
                features["fp_teammate_gap_pct"] = (own / mate - 1) * 100
        long_runs = practice.loc[practice["SessionType"].eq("FP2")].copy()
        if not long_runs.empty and "Stint" in long_runs:
            correction = ["SessionStartUtc", "_pace_time_bin"] + [
                column for column in ("Compound", "TyreLife") if column in long_runs]
            cohorts = long_runs.groupby(correction, dropna=False)
            comparable = cohorts.Abbreviation.transform("nunique").ge(2)
            long_runs["pace"] = (long_runs.seconds - cohorts.seconds.transform("median")).where(comparable)
            stints = long_runs.groupby(["Abbreviation", "Stint"])["pace"].agg(["median", "count"])
            pace = stints.loc[stints["count"].ge(5), "median"].groupby("Abbreviation").median()
            features["fp2_long_run_pace_rank"] = pace.rank(method="min").get(target["Abbreviation"], np.nan)
            own_runs = long_runs.loc[long_runs.Abbreviation.eq(target["Abbreviation"]) & long_runs.pace.notna()]
            features["fp2_long_run_pace_sample_count"] = len(own_runs)
            if not own_runs.empty:
                features["fp2_long_run_pace_mad"] = (own_runs.pace - own_runs.pace.median()).abs().median()
                features["fp2_track_time_coverage"] = own_runs["_pace_time_known"].mean()
    return features


def build_features(results, laps=None, weather=None, messages=None, news=None, stage="post_quali"):
    """Return session-code -> numeric feature tables, with labels and provenance metadata.

    Upcoming targets must be supplied by the caller with a scheduled SessionStartUtc
    and NaN Position/Points/Status. No dates, entrants or outcomes are fabricated.
    GridPosition on a race result is ignored unless GridPublishedAt proves it was
    available before the cutoff; earlier qualifying GridPosition is also accepted.
    """
    if stage not in STAGES:
        raise ValueError(f"Unknown stage {stage!r}; expected one of {STAGES}")
    results = _normalize_results(results)
    laps = _prepare_auxiliary(laps, results)
    weather = _prepare_auxiliary(weather, results)
    messages = _prepare_auxiliary(messages, results)
    news = pd.DataFrame() if news is None else news.copy()
    driver_history = _HistoryIndex(results)
    team_history = _HistoryIndex(results, team=True)
    circuit_history = _HistoryIndex(results.assign(Abbreviation=results["CircuitName"]))
    event_results = _event_groups(results)
    auxiliary = [_event_groups(frame) for frame in (laps, weather, messages)]
    completed_races = results.loc[results["SessionType"].eq("R") & results["Position"].notna()
                                  & results["SessionStartUtc"].notna()].drop_duplicates(
        ["Year", "RoundNumber"])
    era_race_times = {
        era: completed_races.loc[completed_races["Year"].ge(era), "SessionStartUtc"].sort_values().dt.as_unit("ns").astype("int64").to_numpy()
        for era in (2014, 2022, 2026)
    }
    features_by_type = {}

    for target_type in TARGET_TYPES:
        targets = results[results["SessionType"].eq(target_type)]
        if targets.empty:
            continue
        rows = []
        for _, target in targets.iterrows():
            target = target.copy()
            key = (target["Year"], target["RoundNumber"])
            event = event_results[key]
            # Auxiliary-only practice sessions also establish the weekend boundary.
            event_frames = [event] + [group[key] for group in auxiliary if key in group]
            timeline = pd.concat([frame[["SessionType", "SessionStartUtc"]] for frame in event_frames])
            target["FeatureCutoffUtc"] = _stage_cutoff(target, timeline, stage)
            target["stage"] = stage
            known = pd.notna(target["Position"])
            row = {
                "Year": target["Year"],
                "RoundNumber": target["RoundNumber"],
                "EventName": target["EventName"],
                "CircuitName": target["CircuitName"],
                "SessionType": target_type,
                "SessionStartUtc": target["SessionStartUtc"],
                "Abbreviation": target["Abbreviation"],
                "DriverNumber": target.get("DriverNumber", np.nan),
                "TeamName": target["TeamName"],
                "DriverName": normalise_driver_name(target["Abbreviation"], {
                    **build_driver_lookup(event.loc[event["SessionStartUtc"].le(target["FeatureCutoffUtc"])]),
                    **build_driver_lookup(pd.DataFrame([target])),
                }),
                "team_lineage": target["team_lineage"],
                "stage": stage,
                "FeatureCutoffUtc": target["FeatureCutoffUtc"],
                "news_source_max_utc": pd.NaT,
                "target_position": target["Position"],
                "target_points": target["Points"],
                "target_winner": int(target["_winner"]) if known else np.nan,
                "target_podium": int(target["_podium"]) if known else np.nan,
                "target_pole": int(target_type in QUALIFYING_TYPES and target["Position"] == 1) if known else np.nan,
                "target_dnf": target["_dnf"],
                "target_dnf_mechanical": target["_mechanical"],
                "target_dnf_collision": target["_collision"],
                "regulation_era": 2026 if target["Year"] >= 2026 else (2022 if target["Year"] >= 2022 else 2014),
                "rounds_into_era": max(target["RoundNumber"] - 1, 0),
            }
            sources = []
            for category in ("race", "qualifying"):
                for index in (driver_history, team_history):
                    features, source = index.features(target, category)
                    row.update(features)
                    if pd.notna(source):
                        sources.append(source)
            row["history_source_max_utc"] = max(sources) if sources else pd.NaT
            row["team_is_new_entry"] = int(row["constructor_race_career_sessions"] == 0)
            era_times = era_race_times[row["regulation_era"]]
            row["rounds_into_era"] = int(era_times.searchsorted(target["FeatureCutoffUtc"].value, side="left")) if pd.notna(target["FeatureCutoffUtc"]) else 0
            circuit_target = target.copy()
            circuit_target["Abbreviation"] = target["CircuitName"]
            circuit_stats, _ = circuit_history.features(circuit_target, "race")
            row["circuit_overtaking_position_change"] = circuit_stats["driver_race_position_change"]
            row["circuit_dnf_rate_shrunk"] = circuit_stats["driver_race_dnf_rate_shrunk"]
            row["team_dnf_rate_shrunk"] = row["constructor_race_dnf_rate_shrunk"]
            row["driver_incident_rate_shrunk"] = row["driver_race_collision_rate_shrunk"]
            frames = [group.get(key, pd.DataFrame()) for group in auxiliary]
            row.update(_weekend_features(target, target["Abbreviation"], *frames, event))
            row.update(_stage_features(target, event, *frames))
            row.update(_news_features(target, news))
            _neutral_positions(row)
            rows.append(row)
        features_by_type[target_type] = pd.DataFrame(rows)
    return features_by_type


def _load_news():
    path = RAW / "news.csv"
    return pd.read_csv(path) if path.exists() else pd.DataFrame()


def load_laps():
    return _read_parquets("*_laps.parquet")


def load_weather():
    return _read_parquets("*_weather.parquet")


def load_messages():
    return _read_parquets("*_messages.parquet")


def load_news():
    return _load_news()


def load_feature_inputs():
    """Auxiliary kwargs only: build_features(results, stage=stage, **inputs)."""
    return {"laps": load_laps(), "weather": load_weather(),
            "messages": load_messages(), "news": load_news()}


def main():
    results = load_results()
    tables = build_features(results, **load_feature_inputs())
    OUTPUT.mkdir(parents=True, exist_ok=True)
    filenames = {"R": "race", "Q": "qualifying", "S": "sprint", "SQ": "sprint_qualifying"}
    for session_type, features in tables.items():
        output_file = OUTPUT / f"{filenames[session_type]}_features.parquet"
        features.to_parquet(output_file, index=False)
        print(f"Saved {len(features)} {session_type} feature rows to {output_file}")


if __name__ == "__main__":
    main()
