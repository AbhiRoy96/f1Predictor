"""Rate-budget tests with simulated time and no upstream traffic."""

import copy
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests
from fastf1 import req as fastf1_req
from requests.adapters import BaseAdapter
from urllib3.response import HTTPResponse

from src.data import collect_season
from src.data import fastf1_transport as transport


class RequestBudgetTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.cache_dir = Path(self.directory.name)
        self.path = self.cache_dir / "budget.sqlite3"
        self.now = 10000.0
        self.sleeps = []
        clock = patch.object(transport.time, "time", side_effect=lambda: self.now)
        sleeper = patch.object(transport.time, "sleep", side_effect=self.advance)
        clock.start()
        sleeper.start()
        self.addCleanup(clock.stop)
        self.addCleanup(sleeper.stop)
        limits = patch.object(
            fastf1_req._SessionWithRateLimiting, "_RATE_LIMITS",
            copy.deepcopy(fastf1_req._SessionWithRateLimiting._RATE_LIMITS),
        )
        limits.start()
        self.addCleanup(limits.stop)

    def advance(self, delay):
        self.sleeps.append(delay)
        self.now += delay

    def test_jolpica_hourly_budget_is_shared_across_restarts(self):
        budget = transport.RequestBudget(self.path)
        for _ in range(180):
            budget.acquire("https://api.jolpi.ca/ergast/f1")
        self.assertEqual(self.now, 10179)
        restarted = transport.RequestBudget(self.path)
        restarted.acquire("https://api.jolpi.ca/ergast/f1")
        self.assertEqual(self.now, 13601)
        self.assertEqual(self.sleeps[-1], 3422)

    def test_total_hourly_budget_includes_all_hosts(self):
        budget = transport.RequestBudget(self.path)
        for number in range(450):
            budget.acquire(f"https://host{number % 2}.example/data")
        self.assertEqual(self.now, 10449)
        budget.acquire("https://api.jolpi.ca/ergast/f1")
        self.assertEqual(self.now, 13601)
        # A request outside the expired hour can follow after the one-second gap.
        budget.acquire("https://timing.example/data")
        self.assertEqual(self.now, 13602)

    def test_429_cooldown_is_persistent_and_retries_are_bounded(self):
        budget = transport.RequestBudget(self.path)
        request = requests.Request("GET", "https://api.jolpi.ca/ergast/f1").prepare()

        def limited(*args, **kwargs):
            response = requests.Response()
            response.status_code = 429
            response.headers["Retry-After"] = "10"
            response.request = request
            response._content = b"rate limited"
            response.raw = io.BytesIO(response.content)
            return response

        delegate = Mock(spec=BaseAdapter)
        delegate.send.side_effect = limited
        adapter = transport.BudgetAdapter(delegate, budget)
        with self.assertRaises(requests.HTTPError):
            adapter.send(request)
        self.assertEqual(delegate.send.call_count, 3)
        self.assertEqual(self.now, 10020)
        restarted = transport.RequestBudget(self.path)
        restarted.acquire("https://timing.example/data")
        self.assertEqual(self.now, 10030)

    def test_retry_after_date_and_invalid_header(self):
        response = requests.Response()
        response.headers["Retry-After"] = "Thu, 01 Jan 1970 02:47:00 GMT"
        self.assertEqual(transport.retry_delay(response, 2), 20)
        for value in ("not-a-date", "NaN", "inf"):
            response.headers["Retry-After"] = value
            with self.assertLogs(transport.logger, level="WARNING"):
                self.assertEqual(transport.retry_delay(response, 2), 2)

    def test_cache_hits_do_not_consume_budget_or_replace_auth_adapter(self):
        cached = fastf1_req._CachedSessionWithRateLimiting(backend="memory")
        uncached = fastf1_req._SessionWithRateLimiting()
        self.addCleanup(cached.close)
        self.addCleanup(uncached.close)
        delegate = Mock(spec=BaseAdapter)

        def success(request, **kwargs):
            response = requests.Response()
            response.status_code = 200
            response.url = request.url
            response.request = request
            response.headers["Cache-Control"] = "public, max-age=3600"
            response._content = b'{"ok": true}'
            response.raw = HTTPResponse(body=io.BytesIO(response.content), status=200)
            return response

        delegate.send.side_effect = success
        cached.mount("https://timing.example/", delegate)
        cache = SimpleNamespace(_requests_session=uncached, _requests_session_cached=cached)
        transport.install_request_budget(cache, self.cache_dir)
        adapter = cached.get_adapter("https://timing.example/data")
        self.assertIs(adapter.delegate, delegate)
        transport.install_request_budget(cache, self.cache_dir)
        self.assertIs(cached.get_adapter("https://timing.example/data"), adapter)
        self.assertFalse(cached.get("https://timing.example/data").from_cache)
        self.assertTrue(cached.get("https://timing.example/data").from_cache)
        self.assertEqual(delegate.send.call_count, 1)
        self.assertEqual(self.sleeps, [])
        uncached_adapter = uncached.get_adapter("https://another.example/")
        with patch.object(uncached_adapter.delegate, "send", side_effect=success):
            uncached.get("https://another.example/data")
        self.assertEqual(sum(self.sleeps), 1)

    def test_installed_fastf1_native_limits_remain_enabled_and_are_not_hit(self):
        session = fastf1_req._SessionWithRateLimiting()
        self.addCleanup(session.close)
        delegate = Mock(spec=BaseAdapter)

        def success(request, **kwargs):
            response = requests.Response()
            response.status_code = 200
            response.request = request
            response._content = b"ok"
            response._content_consumed = True
            return response

        delegate.send.side_effect = success
        session.mount("https://", delegate)
        transport.install_request_budget(
            SimpleNamespace(_requests_session=session, _requests_session_cached=None),
            self.cache_dir,
        )
        for _ in range(181):
            session.get("https://api.jolpi.ca/ergast/f1")
        self.assertEqual(self.now, 13601)
        self.assertEqual(delegate.send.call_count, 181)
        for _ in range(450):
            session.get("https://timing.example/data")
        self.assertEqual(self.now, 17202)
        self.assertEqual(delegate.send.call_count, 631)

    def test_transport_changes_fail_explicitly_instead_of_disabling_protection(self):
        with self.assertRaisesRegex(RuntimeError, "cannot apply rate protection"):
            transport.install_request_budget(SimpleNamespace(), self.cache_dir)

    def test_detailed_load_retries_transient_errors_but_not_permanent_errors(self):
        session = SimpleNamespace(load=Mock(side_effect=[requests.Timeout("timeout"), None]))
        collect_season._load_session(session, False)
        self.assertEqual(session.load.call_count, 2)
        self.assertEqual(self.sleeps, [1])
        session.load.assert_called_with(laps=True, telemetry=False, weather=True, messages=True)
        response = requests.Response()
        response.status_code = 403
        session.load = Mock(side_effect=requests.HTTPError("forbidden", response=response))
        with self.assertRaises(requests.HTTPError):
            collect_season._load_session(session, False)
        self.assertEqual(session.load.call_count, 1)


if __name__ == "__main__":
    unittest.main()
