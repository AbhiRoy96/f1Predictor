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
LIMITS = {"n_simulations": (100, 100000), "seed": (0, 2147483647),
          "events_per_season": (1, 30)}


def validate_options(data: dict, *, prediction: bool = False) -> dict:
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object.")
    allowed = set(DEFAULTS) | ({"year", "event"} if prediction else set())
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
    return result


def validate_year(value, minimum: int = 1950) -> int:
    if type(value) is not int or not minimum <= value <= datetime.now(timezone.utc).year + 1:
        raise ValueError(f"year must be an integer between {minimum} and next year.")
    return value
