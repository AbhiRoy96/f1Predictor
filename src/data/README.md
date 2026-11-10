# F1 session data pipeline

## Collecting data

From the repository root, collect scheduled practice, qualifying, sprint qualifying, sprint, and race sessions for the default seasons (2018–2026, excluding 2020):

```powershell
python -m src.data.collect_season
```

The same entry point supports an inclusive year range, selected seasons, or one event across the selected seasons. A range includes every year between its endpoints, including 2020:

```powershell
python -m src.data.collect_season --start-year 2018 --end-year 2026
python -m src.data.collect_season --start-year 2021 --end-year 2026 --event "Brazilian Grand Prix"
```

Use `--year` more than once for non-contiguous seasons, including explicit selection of 2020. Do not combine `--year` with the range options; both `--start-year` and `--end-year` are required, and the start must not be later than the end. With no year options, the collector keeps the default seasons (2018–2026, excluding 2020).

Event lookup accepts a scheduled event name, location, or country, ignoring case, accents, and a trailing `Grand Prix`/`GP`; `Brazilian Grand Prix` is also recognized as Brazil. If a location or country matches multiple events, use the exact scheduled event name to choose the intended round. Sessions are loaded by the resolved schedule round number so season-specific event-name changes do not break collection.

The collector writes one parquet file per year and session under `data/raw`: `*_results.parquet`, `*_laps.parquet`, `*_weather.parquet`, and `*_messages.parquet`; it also stores the event schedule in `*_schedule.parquet`. Result and observation rows include year, round, event, session type, and the scheduled UTC session start when FastF1 supplies it. A failure or future start in one session does not block other sessions or replace their saved files.

## Building prediction features

Run feature engineering after collection:

```powershell
python -m src.features.engineer_features
```

Separate files are written to `data/processed`: `race_features.parquet`, `sprint_features.parquet`, `qualifying_features.parquet`, and `sprint_qualifying_features.parquet` when those target sessions exist. Target outputs use `target_*` columns for the session outcome. Historical driver and constructor features, same-weekend lap/tyre summaries, weather, and race-control messages are filtered to observations from sessions whose scheduled UTC start is strictly earlier than the target session start. Do not treat `target_*` values as model inputs.

An optional `data/raw/news.csv` may provide timestamped reports. It needs a `PublishedAt` (or `published_at` / `PublishedUTC`) timestamp and should identify the event with `EventName` or `CircuitName`, or supply a `Year`. Only reports published before the target session start contribute to the news counts; the pipeline does not fetch, infer, or backdate news.

## Master driver and constructor lists

The collection command refreshes `data/processed/drivers.csv` and `data/processed/constructors.csv` once after all requested seasons/events have been attempted. These inventories are built from every existing `*_results.parquet` file, including legacy race-only results and sessions from outside the requested range, so older driver and constructor names remain available. If no result files exist, existing inventories are left unchanged. A collection or refresh failure is reported and the command exits nonzero after attempting the remaining years; run `python -m src.features.engineer_features` separately to build model features from the collected results.

## Data and coverage notes

- FastF1 history does not provide every session type, weather observation, message, or lap for every season. Missing evidence remains missing in features rather than being fabricated.
- Legacy race parquet files without session timestamps are ordered using saved schedule timestamps when available; otherwise a deterministic year/round/session fallback is used. That fallback cannot recover the true time of day and is less precise than collected schedule metadata.
- Circuit history uses FastF1 `Location` when available, otherwise `EventName`. Event naming and circuit identities can change between seasons, so identity aliases may need a curated mapping for a production training set.
- Session result positions are kept in target columns and only earlier race/sprint or qualifying sessions feed their corresponding historical form features. Same-session classifications are never used as features for their own target.
