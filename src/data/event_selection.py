"""Bounded event selection using explicit, heuristic circuit families."""

import pandas as pd

from src.data.collect_season import _normalize_event_identifier
from src.data.normalise_entities import normalise_circuit

DEFAULT_EVENTS_PER_SEASON = 6

# These are collection presets, not learned similarity scores or model features.
CIRCUIT_FAMILIES = (
    ("interlagos", "austin", "spielberg", "barcelona", "silverstone", "suzuka"),
    ("monza", "spa", "baku", "las vegas", "jeddah"),
    ("monaco", "singapore", "hungaroring", "zandvoort"),
    ("bahrain", "yas marina", "albert park", "shanghai", "sepang"),
)


def circuit_key(value):
    return _normalize_event_identifier(normalise_circuit(value))


def event_circuit(event):
    for column in ("CircuitName", "Location", "EventName"):
        value = event.get(column)
        if pd.notna(value) and str(value).strip():
            return circuit_key(value)
    raise ValueError("An event requires a circuit, location or event name")


def similar_circuit_keys(target, override=None):
    """Return user-supplied circuits, or the target's documented preset family."""
    own = event_circuit(target)
    if override is not None:
        return [key for key in dict.fromkeys(map(circuit_key, override)) if key != own]
    for family in CIRCUIT_FAMILIES:
        keys = list(map(circuit_key, family))
        if own in keys:
            return [key for key in keys if key != own]
    return []


def select_related_events(schedule, target, collection_year, target_year,
                          max_events=DEFAULT_EVENTS_PER_SEASON, similar_circuits=None):
    """Select a target, two predecessors, two similar circuits, then predecessors.

    The cap includes the target event. In the target season no later rounds are
    eligible. Older seasons may contribute similar circuits later in their calendar.
    """
    if isinstance(max_events, bool) or not isinstance(max_events, int) or max_events < 1:
        raise ValueError("events_per_season must be a positive integer")
    if collection_year > target_year:
        raise ValueError("Cannot select events from a future season")
    events = schedule.loc[pd.to_numeric(schedule.RoundNumber, errors="coerce").gt(0)].copy()
    events["RoundNumber"] = pd.to_numeric(events.RoundNumber).astype(int)
    events = events.sort_values("RoundNumber").drop_duplicates("RoundNumber")
    if collection_year == target_year:
        events = events.loc[events.RoundNumber.le(int(target.RoundNumber))].copy()
    if events.empty:
        return events
    events["_circuit"] = events.apply(event_circuit, axis=1)
    own = events.loc[events["_circuit"].eq(event_circuit(target))]
    exact = events.loc[events.EventName.eq(target.EventName)]
    anchor = exact.iloc[-1] if not exact.empty else (own.iloc[-1] if not own.empty else None)
    anchor_round = int(anchor.RoundNumber) if anchor is not None else int(target.RoundNumber)
    preceding = events.loc[events.RoundNumber.lt(anchor_round)].sort_values("RoundNumber", ascending=False)
    similar = events.loc[events["_circuit"].isin(similar_circuit_keys(target, similar_circuits))]
    similar = similar.assign(_distance=(similar.RoundNumber - anchor_round).abs()).sort_values(
        ["_distance", "RoundNumber"]).drop(columns="_distance")
    candidates = []
    if anchor is not None:
        candidates.append(int(anchor.RoundNumber))
    candidates.extend(preceding.RoundNumber.head(2))
    candidates.extend(similar.loc[~similar.RoundNumber.isin(candidates)].RoundNumber.head(2))
    candidates.extend(preceding.RoundNumber)
    candidates.extend(similar.RoundNumber)
    selected = list(dict.fromkeys(candidates))[:max_events]
    return events.loc[events.RoundNumber.isin(selected)].drop(columns="_circuit")
