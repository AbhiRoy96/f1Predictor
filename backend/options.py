"""Strict, shared validation of UI settings and queued prediction inputs."""

from datetime import datetime, timezone

DEFAULTS = {
    "session": "race", "stage": "post_quali", "strength_model": "ensemble",
    "n_simulations": 20000, "seed": 42, "events_per_season": 6,
    "skip_collection": False,
}
CHOICES = {
    "session": ("race", "sprint", "qualifying"),
    "stage": ("pre_weekend", "post_fp", "post_quali"),
    "strength_model": ("ensemble", "bayesian", "legacy"),
}
LIMITS = {"n_simulations": (100, 50000), "seed": (0, 2147483647),
          "events_per_season": (1, 12)}


def validate_options(data: dict, *, prediction: bool = False) -> dict:
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object.")
    allowed = set(DEFAULTS) | (
        {"year", "event", "forecast_phase", "source_published_at_utc"} if prediction else set())
    unknown = set(data) - allowed
    if unknown:
        raise ValueError("Unknown fields: " + ", ".join(sorted(unknown)))
    result = DEFAULTS | data
    for key, choices in CHOICES.items():
        if result[key] not in choices:
            raise ValueError(f"{key} must be one of {', '.join(choices)}.")
    for key, (low, high) in LIMITS.items():
        if type(result[key]) is not int or not low <= result[key] <= high:
            raise ValueError(f"{key} must be an integer between {low} and {high}.")
    if type(result["skip_collection"]) is not bool:
        raise ValueError("skip_collection must be boolean.")
    if prediction:
        result["year"] = validate_year(result.get("year"), minimum=2021)
        event = result.get("event")
        if not isinstance(event, str) or not event.strip() or len(event) > 160:
            raise ValueError("event must be a nonempty name of at most 160 characters.")
        result["event"] = event.strip()
        phase = result.get("forecast_phase")
        published = result.get("source_published_at_utc")
        if phase is not None or published is not None:
            if not isinstance(phase, str) or phase not in {"post_quali", "official_grid"} or result["stage"] != "post_quali":
                raise ValueError("Scheduled forecast_phase must be post_quali or official_grid with stage=post_quali.")
            if not isinstance(published, str):
                raise ValueError("Scheduled forecasts require source_published_at_utc with a timezone.")
            try:
                timestamp = datetime.fromisoformat(published.replace("Z", "+00:00"))
            except ValueError as error:
                raise ValueError("source_published_at_utc must be an ISO timestamp with a timezone.") from error
            if timestamp.tzinfo is None or timestamp > datetime.now(timezone.utc):
                raise ValueError("source_published_at_utc must include a timezone and cannot be in the future.")
            result["source_published_at_utc"] = timestamp.isoformat()
        else:
            result.pop("forecast_phase", None)
            result.pop("source_published_at_utc", None)
    return result


def validate_year(value, minimum: int = 1950) -> int:
    if type(value) is not int or not minimum <= value <= datetime.now(timezone.utc).year + 1:
        raise ValueError(f"year must be an integer between {minimum} and next year.")
    return value
