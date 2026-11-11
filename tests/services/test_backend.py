"""API, persistence and worker contracts, with optional real PostgreSQL coverage."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd
from sqlalchemy import select

from backend.app import create_app
from backend.db import metadata, migrate, save_run, seasons, settings, upsert, utcnow
from backend.options import DEFAULTS
from backend.tasks import execute_job, fetch_table, import_local_data, sync_season
from backend.worker import process_next
from scripts.export_web_data import _reports, build_demo_frame


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.url = "sqlite:///" + str(Path(self.directory.name) / "test.db")
        self.app = create_app({"TESTING": True, "DATABASE_URL": self.url})
        self.engine = self.app.extensions["db"]
        migrate(self.engine)
        self.client = self.app.test_client()

    def tearDown(self):
        self.engine.dispose()
        self.directory.cleanup()

    def submit(self, **overrides):
        return self.client.post("/api/predictions", json={"year": 2026, "event": "Brazilian Grand Prix"} | overrides)

    def test_schema_is_idempotent_and_empty(self):
        migrate(self.engine)
        self.assertEqual(self.client.get("/api/health").status_code, 200)
        self.assertEqual(self.client.get("/api/runs").json, [])
        self.assertEqual(self.client.get("/api/seasons").json, [])
        self.assertFalse(self.client.get("/api/settings").json["credentials_required"])
        self.assertEqual(self.client.get("/api/runs/missing").status_code, 404)
        self.assertEqual(self.client.get("/api/jobs/missing").status_code, 404)
        self.assertEqual(self.client.get("/api/seasons/2026").status_code, 404)

    def test_settings_persist_and_are_snapshotted_per_job(self):
        values = DEFAULTS | {"n_simulations": 1000, "strength_model": "bayesian"}
        self.assertEqual(self.client.put("/api/settings", json=values).status_code, 200)
        result = self.submit()
        self.assertEqual(result.status_code, 202)
        self.client.put("/api/settings", json=DEFAULTS)
        job = self.client.get(f"/api/jobs/{result.json['id']}").json
        self.assertEqual(job["payload"]["n_simulations"], 1000)
        restarted = create_app({"TESTING": True, "DATABASE_URL": self.url})
        try:
            self.assertEqual(restarted.test_client().get("/api/settings").json["values"], DEFAULTS)
            self.assertEqual(len(restarted.test_client().get("/api/jobs").json), 1)
        finally:
            restarted.extensions["db"].dispose()

    def test_invalid_inputs_never_enqueue(self):
        bad = [
            {"year": True}, {"year": 2020}, {"year": 5000}, {"event": ""},
            {"event": "x" * 161}, {"event": ["not", "text"]}, {"n_simulations": True},
            {"n_simulations": 99}, {"n_simulations": 100001}, {"seed": -1},
            {"session": "shell"}, {"stage": "future"}, {"strength_model": "unknown"},
            {"events_per_season": 0}, {"skip_collection": "yes"}, {"api_key": "not-supported"},
        ]
        for data in bad:
            with self.subTest(data=data):
                self.assertEqual(self.submit(**data).status_code, 400)
        for data in ([], None, "invalid"):
            self.assertEqual(self.client.post("/api/predictions", json=data).status_code,
                             415 if data is None else 400)
        self.assertEqual(self.client.post("/api/predictions", data="{}").status_code, 415)
        self.assertEqual(self.client.post("/api/predictions", data="{", content_type="application/json").status_code, 400)
        self.assertEqual(self.client.put("/api/settings", json={"password": "unsupported"}).status_code, 400)
        self.assertEqual(self.client.get("/api/jobs").json, [])

    def test_queue_cap_and_sync_validation(self):
        self.assertEqual(self.client.post("/api/seasons/2026/sync", json={"unknown": 1}).status_code, 400)
        self.assertEqual(self.client.post("/api/seasons/1900/sync", json={}).status_code, 400)
        for _ in range(10):
            self.assertEqual(self.submit().status_code, 202)
        self.assertEqual(self.submit().status_code, 429)
        self.assertEqual(self.client.post("/api/seasons/2026/sync", json={}).status_code, 429)

    def test_historical_seasons_start_in_1950_without_changing_prediction_limits(self):
        response = self.client.post("/api/seasons/1950/sync", json={})
        self.assertEqual(response.status_code, 202)
        self.assertEqual(self.client.post("/api/seasons/1949/sync", json={}).status_code, 400)
        self.assertEqual(self.submit(year=1950).status_code, 400)

    def test_worker_success_and_failure_are_visible(self):
        job_id = self.submit().json["id"]
        with patch("backend.worker.execute_job", return_value="saved-run") as execute:
            self.assertTrue(process_next(self.engine))
            execute.assert_called_once()
        job = self.client.get(f"/api/jobs/{job_id}").json
        self.assertEqual((job["status"], job["run_id"]), ("succeeded", "saved-run"))
        self.assertIsNotNone(job["finished_at"])
        failure_id = self.submit().json["id"]
        with patch("backend.worker.execute_job", side_effect=ValueError("No eligible entrants")):
            self.assertTrue(process_next(self.engine))
        job = self.client.get(f"/api/jobs/{failure_id}").json
        self.assertEqual(job["status"], "failed")
        self.assertIn("No eligible entrants", job["error"])
        self.assertFalse(process_next(self.engine))

    def test_prediction_job_reuses_pipeline_and_stores_contract(self):
        frame = build_demo_frame(n_simulations=100)
        payload = DEFAULTS | {"year": 2026, "event": "Brazilian Grand Prix"}
        with patch("main.run_pipeline", return_value=frame) as pipeline:
            result = execute_job(self.engine, {"id": "test-run", "kind": "prediction", "payload": payload})
        pipeline.assert_called_once_with(**payload)
        self.assertEqual(result, "test-run")
        response = self.client.get("/api/runs/test-run")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json["drivers"]), 22)
        self.assertAlmostEqual(sum(row["win_probability"] for row in response.json["drivers"]), 1)
        self.assertEqual(self.client.get("/api/runs").json[0]["id"], "test-run")

    def test_import_order_does_not_change_forecast_chronology(self):
        for run_id, timestamp in (("newer", "2026-03-02T00:00:00Z"),
                                  ("older", "2026-03-01T00:00:00Z")):
            with self.engine.begin() as connection:
                save_run(connection, {"meta": {"id": run_id, "prediction_generated_at_utc": timestamp}})
        self.assertEqual([row["id"] for row in self.client.get("/api/runs").json], ["newer", "older"])
        frame = build_demo_frame(n_simulations=100)
        frame["prediction_generated_at_utc"] = pd.Timestamp("2026-03-02T00:00:00Z")
        from scripts.export_web_data import build_run

        payload = build_run(frame, "timestamp-contract")
        self.assertIsInstance(payload["meta"]["prediction_generated_at_utc"], str)
        with self.engine.begin() as connection:
            save_run(connection, payload)

    def test_pagination_merges_split_race_classifications(self):
        first = {"season": "2026", "round": "1", "Results": [{"position": "1"}]}
        second = first | {"Results": [{"position": "2"}]}
        responses = []
        for row in (first, second):
            response = Mock()
            response.json.return_value = {"MRData": {"total": "2", "limit": "1",
                                                     "RaceTable": {"Races": [row]}}}
            responses.append(response)
        with patch("backend.tasks.requests.get", side_effect=responses) as get:
            result = fetch_table(2026, "results", "RaceTable", "Races")
        self.assertEqual([row["position"] for row in result[0]["Results"]], ["1", "2"])
        self.assertEqual(get.call_args_list[1].kwargs["params"]["offset"], 1)

    def test_sync_is_atomic_and_uses_calendar_endpoint(self):
        payload = {"source": "previous"}
        with self.engine.begin() as connection:
            upsert(connection, seasons, {"year": 2026}, {"payload": payload, "updated_at": utcnow()})
        with patch("backend.tasks.fetch_table", side_effect=[[], RuntimeError("Provider unavailable")]):
            with self.assertRaises(RuntimeError):
                sync_season(self.engine, 2026)
        self.assertEqual(self.client.get("/api/seasons/2026").json["payload"], payload)
        with patch("backend.tasks.fetch_table", return_value=[]):
            sync_season(self.engine, 2026)
        self.assertEqual(self.client.get("/api/seasons/2026").json["payload"]["source"], "jolpica")
        response = Mock()
        response.json.return_value = {"MRData": {"total": "0", "limit": "100", "RaceTable": {"Races": []}}}
        with patch("backend.tasks.requests.get", return_value=response) as get:
            fetch_table(2026, "", "RaceTable", "Races")
        self.assertEqual(get.call_args.args[0], "https://api.jolpi.ca/ergast/f1/2026.json")

    def test_local_import_is_idempotent_and_does_not_replace_synced_history(self):
        root = Path(self.directory.name)
        raw = root / "data" / "raw"
        predictions = root / "data" / "predictions"
        raw.mkdir(parents=True)
        predictions.mkdir()
        pd.DataFrame([{"RoundNumber": 1, "EventName": "Test GP"}]).to_parquet(raw / "2026_schedule.parquet")
        pd.DataFrame([{"RoundNumber": 1, "Abbreviation": "AAA"}]).to_parquet(raw / "2026_race_results.parquet")
        build_demo_frame(n_simulations=100).to_parquet(predictions / "predictions_saved.parquet")
        for _ in range(2):
            self.assertEqual(import_local_data(self.engine, root), {"runs": 1, "seasons": 1})
        self.assertEqual(len(self.client.get("/api/runs").json), 1)
        season = self.client.get("/api/seasons/2026").json["payload"]
        self.assertEqual(season["coverage"], "partial_collected_sessions")
        self.assertEqual(len(season["local_results"]), 1)
        with patch("backend.tasks.fetch_table", return_value=[]):
            sync_season(self.engine, 2026)
        import_local_data(self.engine, root)
        self.assertEqual(self.client.get("/api/seasons/2026").json["payload"]["source"], "jolpica")

    def test_reports_match_the_model_variant(self):
        root = Path(self.directory.name)
        meta = pd.Series({"session": "race", "year": 2026, "round_number": 19,
                          "stage": "post_quali", "strength_model": "ensemble"})
        pd.DataFrame([{"model": "ensemble"}]).to_parquet(
            root / "walk_forward_race_2026_rnd19_post_quali_ensemble_summary.parquet")
        pd.DataFrame([{"model": "legacy"}]).to_parquet(
            root / "walk_forward_race_2026_rnd19_post_quali_summary.parquet")
        self.assertEqual(_reports(meta, root)["summary"][0]["model"], "ensemble")

    def test_home_is_independent_of_forecasts_and_static_exports(self):
        root = Path(__file__).parents[2]
        home = (root / "web" / "src" / "app" / "page.tsx").read_text(encoding="utf-8")
        for forbidden in ("loadRun", "getRun", "win_probability", "RunPicker", "Up next", "DataNotice"):
            self.assertNotIn(forbidden, home)
        data = (root / "web" / "src" / "lib" / "data.ts").read_text(encoding="utf-8")
        self.assertNotIn("node:fs", data)
        telemetry = (root / "web" / "src" / "lib" / "telemetry.ts").read_text(encoding="utf-8")
        self.assertIn("backendRequest", data)
        self.assertIn('cache: "no-store"', telemetry)


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"), "Set TEST_DATABASE_URL to an empty disposable PostgreSQL database")
class PostgresTests(unittest.TestCase):
    def test_jsonb_persistence_and_worker_claim(self):
        app = create_app({"TESTING": True, "DATABASE_URL": os.environ["TEST_DATABASE_URL"]})
        engine = app.extensions["db"]
        if not engine.url.database or not engine.url.database.endswith("_test"):
            engine.dispose()
            self.fail("TEST_DATABASE_URL must use a disposable database name ending in _test.")
        try:
            # This database must be disposable; never point the test URL at app data.
            metadata.drop_all(engine)
            migrate(engine)
            migrate(engine)
            client = app.test_client()
            self.assertEqual(client.get("/api/health").status_code, 200)
            self.assertEqual(client.put("/api/settings", json=DEFAULTS).status_code, 200)
            job_id = client.post("/api/seasons/2026/sync", json={}).json["id"]
            with patch("backend.worker.execute_job", return_value=None):
                self.assertTrue(process_next(engine))
            self.assertEqual(client.get(f"/api/jobs/{job_id}").json["status"], "succeeded")
            with engine.begin() as connection:
                save_run(connection, {"meta": {"id": "persisted"}, "drivers": [], "scenarios": {}, "reports": {}})
                self.assertEqual(connection.scalar(select(settings.c.payload)), DEFAULTS)
            self.assertEqual(client.get("/api/runs/persisted").json["meta"]["id"], "persisted")
        finally:
            metadata.drop_all(engine)
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
