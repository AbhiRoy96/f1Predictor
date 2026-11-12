"""Run with flask --app backend.app:create_app run, or Gunicorn in a container."""

import hashlib
import hmac
import json
import os
import re
from datetime import timedelta, timezone
from uuid import uuid4

import click
from flask import Flask, g, jsonify, request
from opentelemetry import metrics
from opentelemetry.propagate import inject
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.exceptions import HTTPException

from backend.credentials import CredentialsError, account_status, save_account
from backend.db import (
    jobs,
    make_engine,
    migrate,
    request_limits,
    runs,
    seasons,
    settings,
    upsert,
    utcnow,
)
from backend.options import CHOICES, DEFAULTS, LIMITS, validate_options, validate_year
from src.observability.telemetry import configure

SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]{1,160}$")


def create_app(config: dict | None = None) -> Flask:
    telemetry_enabled = configure("gridcast-api")
    app = Flask(__name__)
    app.config.update(
        MAX_CONTENT_LENGTH=16384, HOSTED_MODE=os.getenv("HOSTED_MODE") == "true",
        PROXY_AUTH_TOKEN=os.getenv("PROXY_AUTH_TOKEN", ""),
        USER_QUEUE_LIMIT=3, QUEUE_LIMIT=10, FRESH_RUN_SECONDS=3600,
        SYNC_INTERVAL_SECONDS=3600, WRITE_RATE_PER_MINUTE=20,
    )
    if config:
        app.config.update(config)
    if app.config["HOSTED_MODE"] and len(app.config["PROXY_AUTH_TOKEN"]) < 32:
        raise RuntimeError("Hosted API requires a PROXY_AUTH_TOKEN of at least 32 characters.")
    engine = make_engine(app.config.get("DATABASE_URL"))
    app.extensions["db"] = engine
    if telemetry_enabled:
        from opentelemetry.instrumentation.flask import FlaskInstrumentor

        FlaskInstrumentor().instrument_app(app, excluded_urls="/api/health")

    @app.errorhandler(ValueError)
    def invalid_input(error):
        app.logger.warning("Invalid API input: %s", error)
        return jsonify(error=str(error)), 400

    @app.errorhandler(HTTPException)
    def http_error(error):
        return jsonify(error=error.description), error.code

    @app.errorhandler(SQLAlchemyError)
    def database_error(error):
        app.logger.exception("Database request failed")
        return jsonify(error="Database unavailable or schema missing. Check backend logs and run db-init."), 503

    @app.errorhandler(CredentialsError)
    def credentials_error(error):
        app.logger.error("F1 credential configuration failed: %s", error)
        return jsonify(error=str(error)), 503

    @app.errorhandler(Exception)
    def unexpected_error(error):
        app.logger.exception("Unexpected API error")
        return jsonify(error="Unexpected backend failure. Check backend logs."), 500

    @app.before_request
    def require_json_writes():
        if request.path == "/api/health":
            return None
        if app.config["HOSTED_MODE"]:
            token = request.headers.get("X-Gridcast-Proxy-Token", "")
            owner = request.headers.get("X-Gridcast-User", "")
            if (not hmac.compare_digest(token, app.config["PROXY_AUTH_TOKEN"])
                    or not re.fullmatch(r"[a-f0-9]{64}", owner)):
                app.logger.warning("Rejected unauthenticated API request")
                return jsonify(error="Authentication required."), 401
            g.owner = owner
            g.client_ip = request.headers.get("X-Gridcast-Client-IP", owner)
            if not re.fullmatch(r"[a-f0-9]{64}", g.client_ip):
                return jsonify(error="Invalid client identity."), 401
        else:
            g.owner = hashlib.sha256((request.remote_addr or "local").encode()).hexdigest()
            g.client_ip = g.owner
        # Browsers cannot issue cross-origin JSON writes without a preflight.
        # No CORS is enabled; Next.js proxies same-origin requests.
        if request.method in {"POST", "PUT"} and not request.is_json:
            return jsonify(error="Content-Type must be application/json."), 415
        if request.method in {"POST", "PUT"}:
            now = utcnow()
            with engine.begin() as connection:
                if engine.dialect.name == "postgresql":
                    connection.execute(text("SELECT pg_advisory_xact_lock(716420)"))
                for identity in {g.owner, g.client_ip}:
                    key = f"write:{identity}"
                    row = connection.execute(select(request_limits).where(
                        request_limits.c.id == key)).mappings().first()
                    start = row["window_start"].replace(tzinfo=timezone.utc) if row else now
                    count = row["count"] if row and now - start < timedelta(minutes=1) else 0
                    if count >= app.config["WRITE_RATE_PER_MINUTE"]:
                        app.logger.warning("API write rate limit exceeded")
                        return jsonify(error="Too many writes. Retry in a minute."), 429, {"Retry-After": "60"}
                    upsert(connection, request_limits, {"id": key},
                           {"window_start": start if count else now, "count": count + 1})
                connection.execute(request_limits.delete().where(
                    request_limits.c.window_start < now - timedelta(days=1)))

    @app.get("/api/health")
    def health():
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            connection.execute(select(jobs.c.id).limit(1))
        return jsonify(status="ok")

    @app.get("/api/runs")
    def list_runs():
        with engine.connect() as connection:
            manifests = connection.scalars(select(runs.c.payload["meta"])
                                           .order_by(runs.c.created_at.desc())).all()
        return jsonify(manifests)

    @app.get("/api/runs/<run_id>")
    def get_run(run_id):
        if not SAFE_ID.fullmatch(run_id):
            raise ValueError("Invalid run ID.")
        with engine.connect() as connection:
            payload = connection.scalar(select(runs.c.payload).where(runs.c.id == run_id))
        if payload is None:
            return jsonify(error="Forecast not found."), 404
        return jsonify(payload)

    @app.get("/api/settings")
    def get_settings():
        with engine.connect() as connection:
            saved = connection.scalar(select(settings.c.payload).where(settings.c.id == 1))
        return jsonify(values=saved or DEFAULTS, choices=CHOICES, limits=LIMITS,
                       credentials_required=False,
                       f1_account=account_status(engine, hosted=app.config["HOSTED_MODE"]))

    @app.put("/api/settings/f1-account")
    def put_f1_account():
        if app.config["HOSTED_MODE"]:
            return jsonify(error="F1 account editing is disabled in hosted mode. Use administrator-managed secrets."), 403
        save_account(engine, request.get_json())
        app.logger.info("Saved F1 account configuration updated")
        return jsonify(f1_account=account_status(engine, hosted=False))

    @app.put("/api/settings")
    def put_settings():
        values = validate_options(request.get_json())
        with engine.begin() as connection:
            upsert(connection, settings, {"id": 1}, {"payload": values})
        return jsonify(values=values)

    def enqueue(kind: str, payload: dict):
        request_key = hashlib.sha256(json.dumps(
            {"kind": kind, "payload": {key: value for key, value in payload.items()
                                       if key not in {"n_simulations", "events_per_season", "skip_collection",
                                                      "source_published_at_utc"}}},
            sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        with engine.begin() as connection:
            # Serialize enqueue requests to enforce the queue cap across API workers.
            if engine.dialect.name == "postgresql":
                connection.execute(text("SELECT pg_advisory_xact_lock(716420)"))
            existing = connection.execute(select(jobs).where(
                jobs.c.request_key == request_key,
                jobs.c.owner == g.owner,
                jobs.c.status.in_(("queued", "running", "succeeded")))
                .order_by(jobs.c.created_at.desc()).limit(1)).mappings().first()
            if existing and (existing["status"] in {"queued", "running"} or (
                    existing["payload"] == payload and existing["finished_at"]
                    and utcnow() - existing["finished_at"].replace(
                        tzinfo=timezone.utc) < timedelta(seconds=app.config["FRESH_RUN_SECONDS"]))):
                return jsonify(id=existing["id"], status=existing["status"],
                               run_id=existing["run_id"], reused=True), (
                                   200 if existing["status"] == "succeeded" else 202)
            if kind == "season_sync":
                syncing = connection.scalar(select(jobs.c.id).where(
                    jobs.c.kind == "season_sync", jobs.c.status.in_(("queued", "running"))).limit(1))
                recent = connection.scalar(select(jobs.c.id).where(
                    jobs.c.kind == "season_sync",
                    jobs.c.payload["year"].as_integer() == payload["year"],
                    jobs.c.created_at > utcnow() - timedelta(seconds=app.config["SYNC_INTERVAL_SECONDS"])
                ).limit(1))
                if syncing or recent:
                    return jsonify(error="Season sync is active or this season was requested recently."), 429
            else:
                active = connection.scalar(select(jobs.c.id).where(
                    jobs.c.request_key == request_key,
                    jobs.c.status.in_(("queued", "running"))).limit(1))
                if active:
                    return jsonify(error="An identical forecast is already queued or running."), 409
            count = connection.scalar(select(func.count()).select_from(jobs).where(
                jobs.c.status.in_(("queued", "running"))))
            owned = connection.scalar(select(func.count()).select_from(jobs).where(
                jobs.c.owner == g.owner, jobs.c.status.in_(("queued", "running"))))
            if count >= app.config["QUEUE_LIMIT"] or owned >= app.config["USER_QUEUE_LIMIT"]:
                return jsonify(error="Job queue is full. Wait for existing jobs to finish."), 429
            job_id = str(uuid4())
            carrier = {}
            inject(carrier)
            connection.execute(jobs.insert().values(
                id=job_id, kind=kind, status="queued", payload=payload, created_at=utcnow(),
                owner=g.owner, request_key=request_key,
                trace_context={key: value for key, value in carrier.items()
                               if key in {"traceparent", "tracestate"}}))
        metrics.get_meter("gridcast.queue").create_counter("gridcast.jobs.enqueued").add(1, {"job.kind": kind})
        app.logger.info("Job queued", extra={"telemetry": {"job.id": job_id, "job.kind": kind}})
        return jsonify(id=job_id, status="queued"), 202

    @app.post("/api/predictions")
    def predict():
        data = request.get_json()
        if not isinstance(data, dict):
            raise ValueError("Expected a JSON object.")
        with engine.connect() as connection:
            saved = connection.scalar(select(settings.c.payload).where(settings.c.id == 1))
        return enqueue("prediction", validate_options((saved or DEFAULTS) | data, prediction=True))

    @app.get("/api/jobs")
    def list_jobs():
        with engine.connect() as connection:
            rows = connection.execute(select(jobs).where(jobs.c.owner == g.owner)
                                      .order_by(jobs.c.created_at.desc()).limit(50)).mappings()
            return jsonify([{key: value for key, value in row.items()
                             if key not in {"owner", "request_key", "trace_context"}} for row in rows])

    @app.get("/api/jobs/<job_id>")
    def get_job(job_id):
        with engine.connect() as connection:
            row = connection.execute(select(jobs).where(
                jobs.c.id == job_id, jobs.c.owner == g.owner)).mappings().first()
        if row is None:
            return jsonify(error="Job not found."), 404
        return jsonify({key: value for key, value in row.items()
                        if key not in {"owner", "request_key", "trace_context"}})

    @app.get("/api/seasons")
    def list_seasons():
        with engine.connect() as connection:
            years = connection.scalars(select(seasons.c.year).order_by(seasons.c.year.desc())).all()
        return jsonify(years)

    @app.get("/api/seasons/<int:year>")
    def get_season(year):
        validate_year(year)
        with engine.connect() as connection:
            row = connection.execute(select(seasons).where(seasons.c.year == year)).mappings().first()
        if row is None:
            return jsonify(error="Season not imported or synced yet."), 404
        return jsonify(dict(row))

    @app.post("/api/seasons/<int:year>/sync")
    def sync_season(year):
        validate_year(year)
        if request.get_json() != {}:
            raise ValueError("Season sync expects an empty JSON object.")
        return enqueue("season_sync", {"year": year})

    @app.cli.command("db-init")
    def db_init():
        """Apply schema migrations; do not generate or seed data."""
        migrate(engine)
        click.echo("Database schema is ready (v3).")

    @app.cli.command("import-data")
    def import_data():
        """Import existing forecasts and locally available historical data."""
        from backend.tasks import import_local_data

        counts = import_local_data(engine)
        click.echo(f"Imported {counts['runs']} forecasts and {counts['seasons']} partial seasons.")

    return app
