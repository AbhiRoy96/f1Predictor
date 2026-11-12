"""Cache-preserving request budgets for FastF1 collection."""

import logging
import math
import sqlite3
import time
from contextlib import closing
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit

import requests
from requests.adapters import BaseAdapter

logger = logging.getLogger(__name__)

_WINDOW = 3601
_TOTAL_BUDGET = 450
_JOLPICA_BUDGET = 180


def retry_delay(response: requests.Response, fallback: float) -> float:
    """Parse Retry-After seconds or an HTTP date, with a logged fallback."""
    value = response.headers.get("Retry-After")
    if not value:
        return fallback
    try:
        try:
            delay = float(value)
        except ValueError:
            delay = parsedate_to_datetime(value).timestamp() - time.time()
        if not math.isfinite(delay):
            raise ValueError("non-finite Retry-After")
        return max(fallback, delay)
    except (ValueError, TypeError, OverflowError):
        logger.warning("Invalid upstream Retry-After %r; using backoff", value)
        return fallback


class RequestBudget:
    """Share conservative rolling-hour budgets across processes using one cache."""

    def __init__(self, path: Path):
        self.path = path
        with closing(sqlite3.connect(path, timeout=30)) as connection, connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS requests (sent REAL NOT NULL, jolpica INTEGER NOT NULL)"
            )
            connection.execute("CREATE INDEX IF NOT EXISTS request_time ON requests(sent)")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS cooldown (id INTEGER PRIMARY KEY, until REAL NOT NULL)"
            )

    def acquire(self, url: str) -> None:
        host = urlsplit(url).hostname or ""
        jolpica = host == "jolpi.ca" or host.endswith(".jolpi.ca")
        while True:
            with closing(sqlite3.connect(self.path, timeout=30)) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                now = time.time()
                connection.execute("DELETE FROM requests WHERE sent <= ?", (now - _WINDOW,))
                rows = connection.execute(
                    "SELECT sent, jolpica FROM requests ORDER BY sent"
                ).fetchall()
                cooldown = connection.execute("SELECT until FROM cooldown WHERE id = 1").fetchone()
                ready = max(now, cooldown[0] if cooldown else now)
                if rows:
                    ready = max(ready, rows[-1][0] + 1)
                if len(rows) >= _TOTAL_BUDGET:
                    ready = max(ready, rows[-_TOTAL_BUDGET][0] + _WINDOW)
                if jolpica:
                    upstream = [sent for sent, is_jolpica in rows if is_jolpica]
                    if len(upstream) >= _JOLPICA_BUDGET:
                        ready = max(ready, upstream[-_JOLPICA_BUDGET] + _WINDOW)
                delay = ready - now
                if delay <= 0:
                    connection.execute(
                        "INSERT INTO requests (sent, jolpica) VALUES (?, ?)", (now, int(jolpica))
                    )
                    return
            if delay > 1:
                logger.warning("FastF1 request budget/cooldown: waiting %.1f seconds", delay)
            time.sleep(delay)

    def defer(self, delay: float) -> None:
        with closing(sqlite3.connect(self.path, timeout=30)) as connection, connection:
            connection.execute(
                "INSERT INTO cooldown (id, until) VALUES (1, ?) "
                "ON CONFLICT(id) DO UPDATE SET until = MAX(until, excluded.until)",
                (time.time() + delay,),
            )


class BudgetAdapter(BaseAdapter):
    """Pace cache misses while retaining FastF1's existing auth/TLS adapters."""

    def __init__(self, delegate: BaseAdapter, budget: RequestBudget):
        self.delegate = delegate
        self.budget = budget

    def send(self, request, **kwargs):
        for attempt in range(3):
            self.budget.acquire(request.url)
            response = self.delegate.send(request, **kwargs)
            if response.status_code != 429:
                return response
            delay = retry_delay(response, 2 ** (attempt + 1))
            self.budget.defer(delay)
            logger.warning("FastF1 upstream returned 429; cooling down for %.1f seconds", delay)
            if attempt == 2:
                response.close()
                response.raise_for_status()
            response.close()

    def close(self):
        self.delegate.close()


def install_request_budget(cache, cache_dir: Path) -> None:
    """Mount on FastF1's transports, below its HTTP cache and native limiters."""
    budget = RequestBudget(cache_dir / "request_budget.sqlite3")
    # FastF1 has no public transport accessor. Keep this compatibility seam here;
    # do not replace its sessions, authentication adapters, or native limiters.
    for name in ("_requests_session", "_requests_session_cached"):
        if not hasattr(cache, name):
            raise RuntimeError(f"FastF1 no longer exposes {name}; cannot apply rate protection")
        session = getattr(cache, name)
        if session is None:
            if name == "_requests_session":
                raise RuntimeError("FastF1 no longer exposes its request transport; cannot apply rate protection")
            continue
        if not isinstance(session, requests.Session):
            raise TypeError(f"Unsupported FastF1 transport: {name}")
        for prefix, adapter in list(session.adapters.items()):
            if not isinstance(adapter, BudgetAdapter):
                session.mount(prefix, BudgetAdapter(adapter, budget))
