"""Write-only, encrypted F1 account configuration for private installations."""

import json
import os
from contextlib import contextmanager

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select

from backend.db import provider_credentials, upsert


class CredentialsError(RuntimeError):
    """The server cannot safely save or load provider credentials."""


def _cipher() -> Fernet:
    key = os.getenv("CREDENTIALS_ENCRYPTION_KEY", "")
    if not key:
        raise CredentialsError("Set CREDENTIALS_ENCRYPTION_KEY on the API and worker before saving an F1 account.")
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError):
        raise CredentialsError("CREDENTIALS_ENCRYPTION_KEY must be a valid Fernet key.") from None


def account_status(engine, *, hosted: bool) -> dict:
    with engine.connect() as connection:
        stored = connection.scalar(select(provider_credentials.c.provider).where(
            provider_credentials.c.provider == "f1"))
    environment = bool(os.getenv("F1_USERNAME") and os.getenv("F1_PASSWORD"))
    error = None
    if not hosted:
        try:
            _cipher()
        except CredentialsError as exc:
            error = str(exc)
    return {
        "configured": (bool(stored) and not hosted) or environment,
        "source": "database" if stored and not hosted else "environment" if environment else "none",
        "editable": not hosted and error is None,
        "hosted": hosted,
        "error": error,
    }


def save_account(engine, data: object) -> None:
    if not isinstance(data, dict):
        raise ValueError("Expected an F1 account JSON object.")  # noqa: TRY004 - API validation uses ValueError for HTTP 400.
    if set(data) == {"clear"} and data["clear"] is True:
        with engine.begin() as connection:
            connection.execute(provider_credentials.delete().where(
                provider_credentials.c.provider == "f1"))
        return
    if set(data) != {"username", "password"}:
        raise ValueError("Provide both username and password, or clear the saved account.")
    username, password = data["username"], data["password"]
    if (not isinstance(username, str) or not username.strip() or len(username) > 320
            or not isinstance(password, str) or not password.strip() or len(password) > 1024):
        raise ValueError("F1 username and password must be nonempty text within the allowed lengths.")
    if "\0" in username or "\0" in password:
        raise ValueError("F1 account fields cannot contain null characters.")
    ciphertext = _cipher().encrypt(json.dumps(
        {"F1_USERNAME": username.strip(), "F1_PASSWORD": password}).encode("utf-8")).decode("ascii")
    with engine.begin() as connection:
        upsert(connection, provider_credentials, {"provider": "f1"}, {"ciphertext": ciphertext})


@contextmanager
def collection_credentials(engine):
    """Expose saved credentials only while a serialized prediction job runs."""
    if os.getenv("HOSTED_MODE") == "true":
        yield
        return
    with engine.connect() as connection:
        ciphertext = connection.scalar(select(provider_credentials.c.ciphertext).where(
            provider_credentials.c.provider == "f1"))
    if ciphertext is None:
        yield
        return
    cipher = _cipher()
    try:
        values = json.loads(cipher.decrypt(ciphertext.encode("ascii")))
    except (InvalidToken, ValueError, UnicodeError):
        raise CredentialsError(
            "Cannot decrypt the saved F1 account. Restore the original encryption key or save the account again."
        ) from None
    if (not isinstance(values, dict) or set(values) != {"F1_USERNAME", "F1_PASSWORD"}
            or not all(isinstance(value, str) and value for value in values.values())):
        raise CredentialsError("Saved F1 account data is invalid. Save the account again.")
    previous = {key: os.environ.get(key) for key in values}
    try:
        os.environ.update(values)
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
