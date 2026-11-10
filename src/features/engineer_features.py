from pathlib import Path
import re
import numpy as np
import pandas as pd

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
    for column, default in (("Position", np.nan), ("Points", 0), ("GridPosition", np.nan)):
        if column not in frame:
            frame[column] = default

    frame["Year"] = pd.to_numeric(frame["Year"], errors="coerce")
    frame["RoundNumber"] = pd.to_numeric(frame["RoundNumber"], errors="coerce").fillna(0)
    frame["SessionType"] = frame["SessionType"].map(_session_code)
    frame["Position"] = pd.to_numeric(frame["Position"], errors="coerce")
    frame["Points"] = pd.to_numeric(frame["Points"], errors="coerce").fillna(0)
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

    missing_start = frame["SessionStartUtc"].isna()
    for index in frame.index[missing_start]:
        row = frame.loc[index]
        year = int(row["Year"])
        round_number = int(row["RoundNumber"])
        rank = SESSION_RANK.get(row["SessionType"], 8)
        frame.at[index, "SessionStartUtc"] = (
            pd.Timestamp(year=year, month=1, day=1, tz="UTC")
            + pd.Timedelta(days=max(round_number - 1, 0) * 7, hours=rank)
        )

    frame["_race_outcome"] = frame["SessionType"].isin(RACE_TYPES)
    frame["_winner"] = (frame["_race_outcome"] & frame["Position"].eq(1)).astype(int)
    frame["_podium"] = (frame["_race_outcome"] & frame["Position"].between(1, 3)).astype(int)
    frame["_category"] = np.select(
        [frame["SessionType"].isin(RACE_TYPES), frame["SessionType"].isin(QUALIFYING_TYPES)],
        ["race", "qualifying"],
        default="other",
    )
    keys = ["Year", "RoundNumber", "SessionType", "Abbreviation"]
    return frame.drop_duplicates(subset=keys, keep="last").sort_values(
        ["SessionStartUtc", "Year", "RoundNumber", "SessionType", "Abbreviation"]
    ).reset_index(drop=True)


def _team_sessions(results):
    eligible = results[results["_category"].isin(["race", "qualifying"])].copy()
    if eligible.empty:
        return pd.DataFrame()
    return (
        eligible.groupby(
            ["Year", "RoundNumber", "SessionType", "SessionStartUtc", "CircuitName", "TeamName"],
            dropna=False,
            as_index=False,
        )
        .agg(
            session_points=("Points", "sum"),
            session_wins=("_winner", "sum"),
            session_podiums=("_podium", "sum"),
            session_avg_position=("Position", "mean"),
        )
        .assign(_category=lambda data: np.where(data["SessionType"].isin(RACE_TYPES), "race", "qualifying"))
    )


def _history_features(history, category, year, circuit, prefix):
    current = history[history["Year"].eq(year)]
    at_circuit = history[history["CircuitName"].astype(str).eq(str(circuit))]
    recent = history.tail(5)
    metrics = {
        "career_starts": len(history),
        "career_wins": int(history["_winner"].sum()),
        "career_podiums": int(history["_podium"].sum()),
        "career_points": float(history["Points"].sum()),
        "career_avg_position": history["Position"].mean(),
        "season_starts": len(current),
        "season_wins": int(current["_winner"].sum()),
        "season_podiums": int(current["_podium"].sum()),
        "season_points": float(current["Points"].sum()),
        "season_avg_position": current["Position"].mean(),
        "circuit_starts": len(at_circuit),
        "circuit_wins": int(at_circuit["_winner"].sum()),
        "circuit_podiums": int(at_circuit["_podium"].sum()),
        "circuit_points": float(at_circuit["Points"].sum()),
        "circuit_avg_position": at_circuit["Position"].mean(),
        "form_3": recent.tail(3)["Position"].mean(),
        "form_5": recent["Position"].mean(),
        "points_5": float(recent["Points"].sum()),
    }
    return {f"{prefix}_{category}_{name}": value for name, value in metrics.items()}


def _team_history_features(history, category, year, circuit):
    current = history[history["Year"].eq(year)]
    at_circuit = history[history["CircuitName"].astype(str).eq(str(circuit))]
    recent = history.tail(5)
    metrics = {
        "career_sessions": len(history),
        "career_wins": int(history["session_wins"].sum()),
        "career_podiums": int(history["session_podiums"].sum()),
        "career_points": float(history["session_points"].sum()),
        "career_avg_position": history["session_avg_position"].mean(),
        "season_sessions": len(current),
        "season_wins": int(current["session_wins"].sum()),
        "season_podiums": int(current["session_podiums"].sum()),
        "season_points": float(current["session_points"].sum()),
        "season_avg_position": current["session_avg_position"].mean(),
        "circuit_sessions": len(at_circuit),
        "circuit_wins": int(at_circuit["session_wins"].sum()),
        "circuit_podiums": int(at_circuit["session_podiums"].sum()),
        "circuit_points": float(at_circuit["session_points"].sum()),
        "circuit_avg_position": at_circuit["session_avg_position"].mean(),
        "points_5": float(recent["session_points"].sum()),
        "form_5": recent["session_avg_position"].mean(),
    }
    return {f"constructor_{category}_{name}": value for name, value in metrics.items()}


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
        lookup = results.dropna(subset=["SessionStartUtc"]).drop_duplicates(
            ["Year", "RoundNumber", "SessionType"]
        ).set_index(["Year", "RoundNumber", "SessionType"])["SessionStartUtc"]
        for index in frame.index[frame["SessionStartUtc"].isna()]:
            row = frame.loc[index]
            key = (row["Year"], row["RoundNumber"], row["SessionType"])
            if key in lookup.index:
                frame.at[index, "SessionStartUtc"] = lookup.loc[key]
    return frame


def _prior_weekend_rows(frame, target):
    if frame.empty or "SessionStartUtc" not in frame:
        return pd.DataFrame()
    same_event = frame["Year"].eq(target["Year"]) & frame["RoundNumber"].eq(target["RoundNumber"])
    earlier = frame["SessionStartUtc"].notna() & frame["SessionStartUtc"].lt(target["SessionStartUtc"])
    return frame[same_event & earlier].copy()


def _seconds(values):
    if pd.api.types.is_numeric_dtype(values):
        return pd.to_numeric(values, errors="coerce")
    return pd.to_timedelta(values, errors="coerce").dt.total_seconds()


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
        driver_laps = prior_laps[prior_laps["Abbreviation"].astype(str).eq(str(abbreviation))]
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
            driver_laps["Compound"].dropna().iloc[-1]
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
    if prior_messages.empty and messages.empty:
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
    mask = published.notna() & published.lt(target["SessionStartUtc"])
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
        "prior_news_item_count": len(selected),
        "prior_news_tyre_mentions": int(text.str.contains(r"tyre|tire", regex=True).sum()),
        "prior_news_weather_mentions": int(text.str.contains(r"weather|rain|wind|temperature", regex=True).sum()),
    }


def build_features(results, laps=None, weather=None, messages=None, news=None):
    """Build target-session tables using only sessions and observations before each start."""
    results = _normalize_results(results)
    laps = _prepare_auxiliary(laps, results)
    weather = _prepare_auxiliary(weather, results)
    messages = _prepare_auxiliary(messages, results)
    news = pd.DataFrame() if news is None else news.copy()
    teams = _team_sessions(results)
    features_by_type = {}

    for target_type in TARGET_TYPES:
        targets = results[results["SessionType"].eq(target_type)]
        if targets.empty:
            continue
        rows = []
        for _, target in targets.iterrows():
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
                "target_position": target["Position"],
                "target_points": target["Points"],
                "target_winner": int(target["_winner"]),
                "target_podium": int(target["_podium"]),
                "target_pole": int(target_type in QUALIFYING_TYPES and target["Position"] == 1),
            }
            start = target["SessionStartUtc"]
            past = results[results["SessionStartUtc"].lt(start)]
            for category, session_types in (("race", RACE_TYPES), ("qualifying", QUALIFYING_TYPES)):
                history = past[
                    past["Abbreviation"].eq(target["Abbreviation"])
                    & past["SessionType"].isin(session_types)
                ].sort_values("SessionStartUtc")
                row.update(_history_features(history, category, target["Year"], target["CircuitName"], "driver"))

                team_history = teams[
                    teams["SessionStartUtc"].lt(start)
                    & teams["TeamName"].eq(target["TeamName"])
                    & teams["_category"].eq(category)
                ].sort_values("SessionStartUtc") if not teams.empty else pd.DataFrame()
                row.update(_team_history_features(team_history, category, target["Year"], target["CircuitName"]))

            row.update(_weekend_features(target, target["Abbreviation"], laps, weather, messages, results))
            row.update(_news_features(target, news))
            rows.append(row)
        features_by_type[target_type] = pd.DataFrame(rows)
    return features_by_type


def _load_news():
    path = RAW / "news.csv"
    return pd.read_csv(path) if path.exists() else pd.DataFrame()


def main():
    results = load_results()
    tables = build_features(
        results,
        laps=_read_parquets("*_laps.parquet"),
        weather=_read_parquets("*_weather.parquet"),
        messages=_read_parquets("*_messages.parquet"),
        news=_load_news(),
    )
    OUTPUT.mkdir(parents=True, exist_ok=True)
    filenames = {"R": "race", "Q": "qualifying", "S": "sprint", "SQ": "sprint_qualifying"}
    for session_type, features in tables.items():
        output_file = OUTPUT / f"{filenames[session_type]}_features.parquet"
        features.to_parquet(output_file, index=False)
        print(f"Saved {len(features)} {session_type} feature rows to {output_file}")


if __name__ == "__main__":
    main()
