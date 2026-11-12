"""Enqueue a forecast through the API; never collect, train, or write local files.

Run with ``python -m scripts.scheduled_forecast --help``. An authenticated service
identity must supply PROXY_AUTH_TOKEN. Qualifying completion and official-grid
publication must be confirmed timestamps, not inferred from scheduled start times.
The API owns durable de-duplication, caps and storage; this client never retries POST.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler


def _utc(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Timestamps must include an explicit timezone")
    return result.astimezone(timezone.utc)


def forecast_payload(year, event, phase, qualifying_completed_at, race_start,
                     grid_published_at=None, *, now=None):
    now = datetime.now(timezone.utc) if now is None else _utc(now)
    qualifying, race = _utc(qualifying_completed_at), _utc(race_start)
    if not qualifying <= now < race:
        raise ValueError("Forecasts may only be queued after confirmed qualifying and before the race")
    if phase not in {"post_quali", "official_grid"}:
        raise ValueError("Unsupported forecast phase")
    source = qualifying
    if phase == "official_grid":
        if not grid_published_at:
            raise ValueError("Official-grid forecasts require confirmed grid publication")
        source = _utc(grid_published_at)
        if not qualifying <= source <= now:
            raise ValueError("Grid publication must follow qualifying and cannot be in the future")
    if type(year) is not int or not 2021 <= year <= now.year + 1 or race.year != year:
        raise ValueError("Invalid forecast year")
    if not isinstance(event, str) or not event.strip() or len(event) > 160:
        raise ValueError("Invalid event name")
    return {"session": "race", "year": year, "event": event.strip(), "stage": "post_quali",
            "strength_model": "ensemble", "n_simulations": 20000, "seed": 42,
            "events_per_season": 6, "skip_collection": False,
            "forecast_phase": phase, "source_published_at_utc": source.isoformat()}


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Refusing API redirect with service credentials")


def enqueue(api_url, token, payload, *, opener=None):
    parsed = urlsplit(api_url)
    local = parsed.hostname in {"localhost", "127.0.0.1", "::1", "backend", "api"}
    if (parsed.scheme != "https" and not (parsed.scheme == "http" and local)) or not parsed.hostname:
        raise ValueError("API URL must use HTTPS (HTTP is allowed only on loopback)")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("API URL cannot contain credentials, query, or fragment")
    if not token or len(token) < 32 or any(char in token for char in "\r\n"):
        raise ValueError("PROXY_AUTH_TOKEN of at least 32 characters is required")
    body = json.dumps(payload, sort_keys=True).encode()
    identity = hashlib.sha256(b"scheduled-forecasts").hexdigest()
    request = Request(api_url.rstrip("/") + "/api/predictions", data=body, method="POST",
                      headers={"Content-Type": "application/json", "X-Gridcast-Proxy-Token": token,
                               "X-Gridcast-User": identity, "X-Gridcast-Client-IP": identity,
                               "Idempotency-Key": hashlib.sha256(body).hexdigest()})
    client = build_opener(_NoRedirect()) if opener is None else opener
    with client.open(request, timeout=30) as response:
        if response.status not in (200, 202):
            raise ValueError(f"Unexpected enqueue status: {response.status}")
        return json.load(response)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default=os.environ.get("BACKEND_URL"), required=not os.environ.get("BACKEND_URL"))
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--event", required=True)
    parser.add_argument("--phase", choices=["post_quali", "official_grid"], required=True)
    parser.add_argument("--qualifying-completed-at", required=True)
    parser.add_argument("--race-start", required=True)
    parser.add_argument("--grid-published-at")
    args = vars(parser.parse_args(argv))
    api_url = args.pop("api_url")
    payload = forecast_payload(**args)
    print(json.dumps(enqueue(api_url, os.environ.get("PROXY_AUTH_TOKEN"), payload)))


if __name__ == "__main__":
    main()
