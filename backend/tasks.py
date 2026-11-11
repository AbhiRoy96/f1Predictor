"""Explicit local imports, complete-season sync and production prediction jobs."""

import json
import time
from pathlib import Path

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from sqlalchemy import select
from urllib3.util.retry import Retry

from backend.db import save_run, seasons, upsert, utcnow

JOLPICA = "https://api.jolpi.ca/ergast/f1"


def records(frame: pd.DataFrame) -> list[dict]:
    return json.loads(frame.to_json(orient="records", date_format="iso"))


def import_local_data(engine, root: Path = Path(".")) -> dict[str, int]:
    from scripts.export_web_data import _reports, build_run

    count = 0
    for path in sorted((root / "data" / "predictions").glob("predictions_*.parquet")):
        frame = pd.read_parquet(path)
        run = build_run(frame, path.stem.removeprefix("predictions_"),
                        _reports(frame.iloc[0], root / "reports"))
        with engine.begin() as connection:
            save_run(connection, run)
        count += 1
    raw = root / "data" / "raw"
    years = set()
    for path in sorted(raw.glob("*_schedule.parquet")):
        frame = pd.read_parquet(path)
        year = int(path.stem.split("_")[0])
        available = []
        for result_path in sorted(raw.glob(f"{year}_*_results.parquet")):
            available.extend(records(pd.read_parquet(result_path)))
        payload = {"source": "local_parquet", "coverage": "partial_collected_sessions",
                   "calendar": records(frame), "local_results": available,
                   "races": [], "sprints": [], "driver_standings": [], "constructor_standings": []}
        with engine.begin() as connection:
            existing = connection.scalar(select(seasons.c.payload).where(seasons.c.year == year))
            # An import must never replace a complete remote season with a partial local sample.
            if existing and existing.get("source") == "jolpica":
                continue
            upsert(connection, seasons, {"year": year}, {"payload": payload, "updated_at": utcnow()})
        years.add(year)
    return {"runs": count, "seasons": len(years)}


def fetch_table(year: int, resource: str, table: str, collection: str) -> list[dict]:
    """Read every page, merging race classifications that span page boundaries."""
    offset = 0
    output = []
    while True:
        suffix = f"/{resource}" if resource else ""
        response = fetch_page(f"{JOLPICA}/{year}{suffix}.json",
                              params={"limit": 100, "offset": offset})
        response.raise_for_status()
        data = response.json()["MRData"]
        rows = data[table][collection]
        output.extend(rows)
        total, limit = int(data["total"]), int(data["limit"])
        if offset + limit >= total:
            break
        if limit <= 0:
            raise ValueError("Season provider returned an invalid pagination limit.")
        offset += limit
    if resource in {"results", "sprint"}:
        merged = {}
        key = "Results" if resource == "results" else "SprintResults"
        for race in output:
            identity = (race["season"], race["round"])
            if identity not in merged:
                merged[identity] = {**race, key: []}
            merged[identity][key].extend(race[key])
        return list(merged.values())
    return output


def fetch_page(url: str, *, params: dict):
    """Retry transient upstream errors with exponential backoff and Retry-After."""
    with requests.Session() as session:
        session.mount("https://", HTTPAdapter(max_retries=Retry(
            total=4, backoff_factor=1, status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods={"GET"}, respect_retry_after_header=True)))
        time.sleep(1)
        return session.get(url, params=params, timeout=(10, 60))


def sync_season(engine, year: int) -> None:
    calendar = fetch_table(year, "", "RaceTable", "Races")
    races = fetch_table(year, "results", "RaceTable", "Races")
    sprints = fetch_table(year, "sprint", "RaceTable", "Races")
    drivers = fetch_table(year, "driverStandings", "StandingsTable", "StandingsLists")
    constructors = fetch_table(year, "constructorStandings", "StandingsTable", "StandingsLists")
    payload = {
        "source": "jolpica", "coverage": "provider_available_results",
        "calendar": calendar, "races": races, "sprints": sprints,
        "driver_standings": drivers, "constructor_standings": constructors,
        "local_results": [],
    }
    with engine.begin() as connection:
        upsert(connection, seasons, {"year": year}, {"payload": payload, "updated_at": utcnow()})


def execute_job(engine, job: dict) -> str | None:
    if job["kind"] == "season_sync":
        sync_season(engine, job["payload"]["year"])
        return None
    if job["kind"] != "prediction":
        raise ValueError(f"Unknown job kind: {job['kind']}")
    from main import run_pipeline
    from scripts.export_web_data import _reports, build_run

    # Reuse the CLI pipeline; its leakage guards, artifacts and model selection remain intact.
    frame = run_pipeline(**job["payload"])
    run_id = job["id"]
    payload = build_run(frame, run_id, _reports(frame.iloc[0], Path("reports")))
    with engine.begin() as connection:
        save_run(connection, payload)
    return run_id
