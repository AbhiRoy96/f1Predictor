"""Encrypted first-run account setup and collection credential boundaries."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.fernet import Fernet
from sqlalchemy import select

from backend.app import create_app
from backend.credentials import CredentialsError, collection_credentials
from backend.db import migrate, provider_credentials
from backend.options import DEFAULTS
from backend.tasks import execute_job
from scripts.export_web_data import build_demo_frame


class F1AccountTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {
            "CREDENTIALS_ENCRYPTION_KEY": Fernet.generate_key().decode(),
            "F1_USERNAME": "", "F1_PASSWORD": "", "HOSTED_MODE": "false",
            "OTEL_ENABLED": "false",
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.url = "sqlite:///" + str(Path(self.directory.name) / "credentials.db")
        self.app = create_app({"TESTING": True, "DATABASE_URL": self.url})
        self.engine = self.app.extensions["db"]
        self.addCleanup(self.engine.dispose)
        migrate(self.engine)
        self.client = self.app.test_client()
        self.account = {"username": "sample@example.test", "password": "test-password with spaces "}

    def save(self, data=None):
        return self.client.put("/api/settings/f1-account", json=self.account if data is None else data)

    def test_account_is_encrypted_persistent_and_write_only(self):
        self.assertFalse(self.client.get("/api/settings").json["f1_account"]["configured"])
        response = self.save()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["f1_account"]["source"], "database")
        with self.engine.connect() as connection:
            ciphertext = connection.scalar(select(provider_credentials.c.ciphertext))
        for secret in self.account.values():
            self.assertNotIn(secret, ciphertext)
            self.assertNotIn(secret, response.get_data(as_text=True))
            self.assertNotIn(secret, self.client.get("/api/settings").get_data(as_text=True))
        restarted = create_app({"TESTING": True, "DATABASE_URL": self.url})
        try:
            self.assertTrue(restarted.test_client().get("/api/settings").json["f1_account"]["configured"])
            with collection_credentials(restarted.extensions["db"]):
                self.assertEqual(os.environ["F1_USERNAME"], self.account["username"])
                self.assertEqual(os.environ["F1_PASSWORD"], self.account["password"])
        finally:
            restarted.extensions["db"].dispose()

    def test_invalid_input_and_missing_key_never_store_plaintext(self):
        for data in ([], {"username": "only"}, {"username": "", "password": "x"},
                     {"username": "x", "password": 123}, {"username": "x", "password": " "},
                     self.account | {"unexpected": True}, {"clear": "true"}, {"clear": 1},
                     self.account | {"password": "bad\0password"}):
            with self.subTest(data_type=type(data).__name__):
                self.assertEqual(self.save(data).status_code, 400)
        for key in ("", "not-a-fernet-key", "\u00e9"):
            with patch.dict(os.environ, {"CREDENTIALS_ENCRYPTION_KEY": key}):
                status = self.client.get("/api/settings").json["f1_account"]
                self.assertFalse(status["editable"])
                self.assertTrue(status["error"])
                response = self.save()
                self.assertEqual(response.status_code, 503)
                self.assertNotIn(self.account["password"], response.get_data(as_text=True))
        with self.engine.connect() as connection:
            self.assertIsNone(connection.scalar(select(provider_credentials.c.ciphertext)))

    def test_hosted_mode_rejects_all_edits_and_ignores_saved_account(self):
        self.assertEqual(self.save().status_code, 200)
        self.app.config.update(HOSTED_MODE=True, PROXY_AUTH_TOKEN="t" * 32)
        headers = {"X-Gridcast-Proxy-Token": "t" * 32, "X-Gridcast-User": "a" * 64}
        for data in (self.account, {"clear": True}):
            response = self.client.put("/api/settings/f1-account", json=data, headers=headers)
            self.assertEqual(response.status_code, 403)
        status = self.client.get("/api/settings", headers=headers).json["f1_account"]
        self.assertFalse(status["editable"])
        self.assertFalse(status["configured"])
        with (
            patch.dict(os.environ, {"HOSTED_MODE": "true", "F1_USERNAME": "admin", "F1_PASSWORD": "managed"}),
            collection_credentials(self.engine),
        ):
            self.assertEqual(os.environ["F1_USERNAME"], "admin")
            self.assertEqual(os.environ["F1_PASSWORD"], "managed")

    def test_remove_restores_environment_credentials_and_works_without_key(self):
        self.save()
        with patch.dict(os.environ, {"F1_USERNAME": "environment-user", "F1_PASSWORD": "environment-password"}):
            with collection_credentials(self.engine):
                self.assertEqual(os.environ["F1_USERNAME"], self.account["username"])
            self.assertEqual(os.environ["F1_USERNAME"], "environment-user")
            with patch.dict(os.environ, {"CREDENTIALS_ENCRYPTION_KEY": ""}):
                response = self.save({"clear": True})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json["f1_account"]["source"], "environment")
            with collection_credentials(self.engine):
                self.assertEqual(os.environ["F1_PASSWORD"], "environment-password")

    def test_wrong_key_and_tampered_ciphertext_fail_explicitly(self):
        self.save()
        with (
            patch.dict(os.environ, {"CREDENTIALS_ENCRYPTION_KEY": Fernet.generate_key().decode()}),
            self.assertRaisesRegex(CredentialsError, "Cannot decrypt"),
            collection_credentials(self.engine),
        ):
            self.fail("An undecryptable account must not reach collection.")
        with self.engine.begin() as connection:
            connection.execute(provider_credentials.update().values(ciphertext="tampered"))
        with self.assertRaisesRegex(CredentialsError, "Cannot decrypt"), collection_credentials(self.engine):
            self.fail("A tampered account must not reach collection.")
        self.assertEqual(os.environ["F1_PASSWORD"], "")

    def test_prediction_jobs_load_latest_account_and_restore_after_failure(self):
        frame = build_demo_frame(n_simulations=100)
        observed = []

        def pipeline(**options):
            observed.append((os.environ["F1_USERNAME"], os.environ["F1_PASSWORD"]))
            return frame

        job = {"id": "credentials-run", "kind": "prediction",
               "payload": DEFAULTS | {"year": 2026, "event": "Brazilian Grand Prix"}}
        with patch("main.run_pipeline", side_effect=pipeline):
            self.save()
            execute_job(self.engine, job)
            self.save({"username": "replacement", "password": "replacement-password"})
            execute_job(self.engine, job)
        self.assertEqual(observed, [(self.account["username"], self.account["password"]),
                                    ("replacement", "replacement-password")])
        self.assertEqual(os.environ["F1_PASSWORD"], "")
        with (
            patch("main.run_pipeline", side_effect=RuntimeError("collection failed")),
            self.assertRaisesRegex(RuntimeError, "collection failed"),
        ):
            execute_job(self.engine, job)
        self.assertEqual(os.environ["F1_USERNAME"], "")
        self.assertEqual(os.environ["F1_PASSWORD"], "")

    def test_skip_collection_does_not_require_the_encryption_key(self):
        self.save()
        frame = build_demo_frame(n_simulations=100)
        with patch.dict(os.environ, {"CREDENTIALS_ENCRYPTION_KEY": ""}), patch(
                "main.run_pipeline", return_value=frame):
            execute_job(self.engine, {"id": "local-run", "kind": "prediction",
                                     "payload": DEFAULTS | {"skip_collection": True}})

    def test_migration_adds_credential_table_to_existing_database(self):
        with self.engine.begin() as connection:
            provider_credentials.drop(connection)
        migrate(self.engine)
        migrate(self.engine)
        self.assertEqual(self.save().status_code, 200)


if __name__ == "__main__":
    unittest.main()
