import argparse
import re
import unicodedata
from pathlib import Path

import fastf1
import pandas as pd

from src.data import collect_driver_constructor_stats

CACHE = Path("data/cache/fastf1")
OUTPUT = Path("data/raw")

_SESSION_CODES = {
    "practice 1": "FP1",
    "practice 2": "FP2",
    "practice 3": "FP3",
    "qualifying": "Q",
    "sprint qualifying": "SQ",
    "sprint shootout": "SQ",
    "sprint": "S",
    "race": "R",
}
_CACHE_ENABLED = False
DEFAULT_SEASONS = tuple(year for year in range(2018, 2027) if year != 2020)


def _enable_cache():
    global _CACHE_ENABLED
    if not _CACHE_ENABLED:
        CACHE.mkdir(parents=True, exist_ok=True)
        configure = getattr(fastf1.Cache, "configure", None)
        if callable(configure):
            configure(cache_dir=str(CACHE))
        else:
            fastf1.Cache.enable_cache(str(CACHE))
        _CACHE_ENABLED = True


def _normalize_event_identifier(value):
    if value is None or pd.isna(value):
        return ""
    normalized = unicodedata.normalize("NFKD", str(value))
    normalized = normalized.encode("ascii", "ignore").decode("ascii").casefold()
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized).strip()
    return re.sub(r"\s+(?:grand\s+prix|gp)$", "", normalized).strip()


def _event_identifier_aliases(value):
    normalized = _normalize_event_identifier(value)
    aliases = {normalized} if normalized else set()
    if normalized == "brazilian":
        aliases.add("brazil")
    return aliases


def _resolve_event(schedule, year, event_name):
    requested = str(event_name).strip()
    exact_matches = schedule[
        schedule["EventName"].astype(str).str.strip().str.casefold()
        == requested.casefold()
    ]
    if len(exact_matches) == 1:
        return exact_matches.iloc[0]

    requested_aliases = _event_identifier_aliases(requested)
    matches = []
    if requested_aliases:
        for _, event in schedule.iterrows():
            event_aliases = set()
            for column in ("EventName", "Location", "Country"):
                if column in event.index:
                    event_aliases.update(_event_identifier_aliases(event[column]))
            if requested_aliases.intersection(event_aliases):
                matches.append(event)

    if len(matches) == 1:
        return matches[0]

    if matches:
        choices = ", ".join(
            f"{event.get('EventName')} (round {event.get('RoundNumber')})"
            for event in matches
        )
        raise ValueError(
            f"Event identifier {event_name!r} is ambiguous in the {year} schedule: "
            f"{choices}. Use an exact event name to select one round."
        )

    available = ", ".join(schedule["EventName"].dropna().astype(str))
    raise ValueError(
        f"Event {event_name!r} was not found in the {year} schedule. "
        f"Available events: {available or 'none'}"
    )


def _session_specs(event):
    specs = []
    for number in range(1, 6):
        name = event.get(f"Session{number}")
        if pd.isna(name) or str(name).strip().lower() in {"", "none", "nan"}:
            continue

        session_type = _SESSION_CODES.get(str(name).strip().lower())
        if session_type is None:
            continue

        start = event.get(f"Session{number}DateUtc")
        if pd.isna(start):
            start = event.get(f"Session{number}Date")
        start = pd.to_datetime(start, utc=True, errors="coerce")
        specs.append((str(name), session_type, start))
    return specs


def _metadata(year, event, session_name, session_type, session_start):
    metadata = {
        "Year": year,
        "EventName": event.get("EventName"),
        "RoundNumber": event.get("RoundNumber"),
        "SessionName": session_name,
        "SessionType": session_type,
        "SessionStartUtc": session_start,
    }
    location = event.get("Location")
    if location is not None and not pd.isna(location):
        metadata["CircuitName"] = location
    return metadata


def _with_metadata(frame, metadata):
    if frame is None:
        return None
    frame = frame.copy()
    if frame.empty:
        return frame
    for column, value in metadata.items():
        frame[column] = value
    return frame


def _persist_frame(frame, path, key_columns):
    if frame is None or frame.empty:
        return

    OUTPUT.mkdir(parents=True, exist_ok=True)
    if path.exists():
        frame = pd.concat([pd.read_parquet(path), frame], ignore_index=True, sort=False)

    keys = [column for column in key_columns if column in frame.columns]
    if keys and frame[keys].notna().to_numpy().any():
        frame = frame.drop_duplicates(subset=keys, keep="last")
    else:
        frame = frame.drop_duplicates(keep="last")
    frame.to_parquet(path, index=False)


def _frame_from_session(session, attribute):
    try:
        frame = getattr(session, attribute)
    except Exception as exc:
        print(f"Could not read {attribute}: {exc}")
        return None
    if frame is None:
        return None
    if not isinstance(frame, pd.DataFrame):
        try:
            frame = pd.DataFrame(frame)
        except (TypeError, ValueError):
            return None
    return frame


def collect_event(year: int, event_name: str, event=None, now=None, raise_on_failure=False,
                  session_types=None):
    """Collect each scheduled session independently for a single event."""
    _enable_cache()
    if event is None:
        schedule = fastf1.get_event_schedule(year, include_testing=False)
        event = _resolve_event(schedule, year, event_name)

    if hasattr(event, "to_dict"):
        event = event.to_dict()
    else:
        event = dict(event)

    schedule_row = pd.DataFrame([event])
    schedule_row["Year"] = year
    _persist_frame(
        schedule_row,
        OUTPUT / f"{year}_schedule.parquet",
        ["Year", "RoundNumber"],
    )

    now = pd.Timestamp.now(tz="UTC") if now is None else pd.to_datetime(now, utc=True)
    collected = []
    failures = []
    for session_name, session_type, session_start in _session_specs(event):
        if session_types is not None and session_type not in session_types:
            continue
        if not pd.isna(session_start) and session_start > now:
            print(f"Skipping future session {event_name} {session_name} ({session_start})")
            continue

        print(f"Collecting {year} {event_name} {session_name}")
        metadata = _metadata(year, event, session_name, session_type, session_start)
        try:
            round_number = event.get("RoundNumber")
            if round_number is not None and not pd.isna(round_number):
                session_identifier = int(round_number)
            else:
                session_identifier = event.get("EventName")
                if not isinstance(session_identifier, str):
                    session_identifier = event_name
            session = fastf1.get_session(year, session_identifier, session_type)
            session.load(laps=True, telemetry=False, weather=True, messages=True)
        except Exception as exc:
            print(f"Skipping {event_name} {session_name}: {exc}")
            failures.append(f"{session_name}: {exc}")
            continue

        results = _with_metadata(_frame_from_session(session, "results"), metadata)
        laps = _with_metadata(_frame_from_session(session, "laps"), metadata)
        weather = _with_metadata(_frame_from_session(session, "weather_data"), metadata)
        messages = _with_metadata(
            _frame_from_session(session, "race_control_messages"), metadata
        )

        if (results is None or results.empty) and (laps is None or laps.empty):
            failures.append(f"{session_name}: no results or lap data returned")
            continue

        stem = f"{year}_{session_type.lower()}"
        _persist_frame(
            results,
            OUTPUT / f"{stem}_results.parquet",
            ["Year", "RoundNumber", "SessionType", "Abbreviation"],
        )
        _persist_frame(
            laps,
            OUTPUT / f"{stem}_laps.parquet",
            ["Year", "RoundNumber", "SessionType", "Driver", "DriverNumber", "LapNumber"],
        )
        _persist_frame(
            weather,
            OUTPUT / f"{stem}_weather.parquet",
            ["Year", "RoundNumber", "SessionType", "Time"],
        )
        _persist_frame(
            messages,
            OUTPUT / f"{stem}_messages.parquet",
            ["Year", "RoundNumber", "SessionType", "Date", "Time", "Message"],
        )
        collected.append(session_type)
        print(f"Finished collecting {year} {event_name} {session_name}")

    if failures and raise_on_failure:
        raise RuntimeError(
            f"Failed to collect {event_name} in {year}: {'; '.join(failures)}"
        )

    return collected


def collect_season(year: int):
    """Collect every scheduled practice, qualifying, sprint, and race session."""
    _enable_cache()
    schedule = fastf1.get_event_schedule(year, include_testing=False)
    _persist_frame(
        schedule.assign(Year=year),
        OUTPUT / f"{year}_schedule.parquet",
        ["Year", "RoundNumber"],
    )

    failures = []
    for _, event in schedule.iterrows():
        event_name = event["EventName"]
        print(f"Collecting {year}: {event_name}")
        try:
            collect_event(year, event_name, event=event, raise_on_failure=True)
        except Exception as exc:
            print(f"Failed to collect {year} {event_name}: {exc}")
            failures.append(f"{event_name}: {exc}")

    if failures:
        raise RuntimeError(f"One or more events failed in {year}: {'; '.join(failures)}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Collect F1 season or event sessions")
    parser.add_argument(
        "--year",
        dest="years",
        type=int,
        action="append",
        metavar="YEAR",
        help="season to collect; may be supplied more than once",
    )
    parser.add_argument(
        "--start-year",
        type=int,
        metavar="YEAR",
        help="first season in an inclusive year range",
    )
    parser.add_argument(
        "--end-year",
        type=int,
        metavar="YEAR",
        help="last season in an inclusive year range",
    )
    parser.add_argument(
        "--event",
        help="collect only this event in each selected season",
    )
    args = parser.parse_args(argv)

    has_start = args.start_year is not None
    has_end = args.end_year is not None
    if has_start != has_end:
        parser.error("--start-year and --end-year must be supplied together")
    if args.years is not None and (has_start or has_end):
        parser.error("--year cannot be combined with --start-year or --end-year")
    if has_start and args.start_year > args.end_year:
        parser.error("--start-year must be less than or equal to --end-year")

    if has_start:
        years = range(args.start_year, args.end_year + 1)
    else:
        years = args.years if args.years is not None else DEFAULT_SEASONS

    failures = []
    for year in years:
        try:
            if args.event:
                collect_event(year, args.event, raise_on_failure=True)
            else:
                collect_season(year)
        except Exception as exc:
            print(f"Failed to collect {year}: {exc}")
            failures.append(f"{year}: {exc}")

    try:
        collect_driver_constructor_stats.collect_driver_constructor_stats()
    except Exception as exc:
        print(f"Failed to refresh driver and constructor lists: {exc}")
        failures.append(f"driver and constructor list refresh: {exc}")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
