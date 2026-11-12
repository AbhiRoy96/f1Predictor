"""Database schema and shared persistence helpers."""

import os
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    func,
    inspect,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine

from src.observability.telemetry import instrument_database

metadata = MetaData()
json_type = JSON().with_variant(JSONB(), "postgresql")
schema_versions = Table("schema_versions", metadata, Column("version", Integer, primary_key=True))
runs = Table(
    "prediction_runs", metadata,
    Column("id", String(160), primary_key=True),
    Column("payload", json_type, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
seasons = Table(
    "season_data", metadata,
    Column("year", Integer, primary_key=True),
    Column("payload", json_type, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
settings = Table(
    "prediction_settings", metadata,
    Column("id", Integer, primary_key=True),
    Column("payload", json_type, nullable=False),
)
provider_credentials = Table(
    "provider_credentials", metadata,
    Column("provider", String(32), primary_key=True),
    Column("ciphertext", Text, nullable=False),
)
jobs = Table(
    "jobs", metadata,
    Column("id", String(36), primary_key=True),
    Column("kind", String(20), nullable=False),
    Column("status", String(20), nullable=False, index=True),
    Column("payload", json_type, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("started_at", DateTime(timezone=True)),
    Column("finished_at", DateTime(timezone=True)),
    Column("run_id", String(160)),
    Column("error", Text),
    Column("trace_context", json_type),
    Column("owner", String(64)),
    Column("request_key", String(64), index=True),
    Column("heartbeat_at", DateTime(timezone=True)),
)
request_limits = Table(
    "request_limits", metadata,
    Column("id", String(80), primary_key=True),
    Column("window_start", DateTime(timezone=True), nullable=False),
    Column("count", Integer, nullable=False),
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def make_engine(url: str | None = None) -> Engine:
    url = url or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL must point to a PostgreSQL database.")
    engine = create_engine(url, pool_pre_ping=True, hide_parameters=True)
    instrument_database(engine)
    return engine


def migrate(engine: Engine) -> None:
    """Apply additive schema migrations without requiring server-admin privileges."""
    if engine.dialect.name == "postgresql" and os.getenv("ENABLE_PG_STAT_STATEMENTS") == "true":
        with engine.begin() as connection:
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS pg_stat_statements"))
    metadata.create_all(engine)
    with engine.begin() as connection:
        version = connection.scalar(select(func.max(schema_versions.c.version))) or 0
        if version > 4:
            raise RuntimeError("Database schema is newer than this application supports.")
        if version < 2:
            columns = {column["name"] for column in inspect(connection).get_columns("jobs")}
            if "trace_context" not in columns:
                kind = "JSONB" if engine.dialect.name == "postgresql" else "JSON"
                connection.execute(text(f"ALTER TABLE jobs ADD COLUMN trace_context {kind}"))
            connection.execute(schema_versions.insert().values(version=2))
        if version < 3:
            columns = {column["name"] for column in inspect(connection).get_columns("jobs")}
            for name, kind in (("owner", "VARCHAR(64)"), ("request_key", "VARCHAR(64)"),
                               ("heartbeat_at", "TIMESTAMP WITH TIME ZONE" if
                                engine.dialect.name == "postgresql" else "DATETIME")):
                if name not in columns:
                    connection.execute(text(f"ALTER TABLE jobs ADD COLUMN {name} {kind}"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_jobs_request_key ON jobs (request_key)"))
            connection.execute(schema_versions.insert().values(version=3))
        if version < 4:
            connection.execute(schema_versions.insert().values(version=4))


def upsert(connection, table: Table, key: dict, values: dict) -> None:
    # All writers are serialized by the worker, except the singleton settings row.
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    insert = pg_insert if connection.dialect.name == "postgresql" else sqlite_insert
    statement = insert(table).values(**key, **values)
    connection.execute(statement.on_conflict_do_update(index_elements=list(key), set_=values))


def save_run(connection, payload: dict) -> None:
    generated = payload["meta"].get("prediction_generated_at_utc")
    created_at = datetime.fromisoformat(generated.replace("Z", "+00:00")) if generated else utcnow()
    if created_at.tzinfo is None:
        raise ValueError("Prediction generation time must include a timezone.")
    upsert(connection, runs, {"id": payload["meta"]["id"]},
           {"payload": payload, "created_at": created_at})
