"""Run with flask --app backend.app:create_app run, or Gunicorn in a container."""

import re
from uuid import uuid4

import click
from flask import Flask, jsonify, request
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.exceptions import HTTPException
from opentelemetry import metrics
from opentelemetry.propagate import inject

from backend.db import jobs, make_engine, migrate, runs, seasons, settings, upsert, utcnow
from backend.options import CHOICES, DEFAULTS, LIMITS, validate_options, validate_year
from src.observability.telemetry import configure

SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]{1,160}$")


def create_app(config: dict | None = None) -> Flask:
    telemetry_enabled = configure("gridcast-api")
    app = Flask(__name__)
    app.config.update(MAX_CONTENT_LENGTH=16384)
    if config:
        app.config.update(config)
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

    @app.errorhandler(Exception)
    def unexpected_error(error):
        app.logger.exception("Unexpected API error")
        return jsonify(error="Unexpected backend failure. Check backend logs."), 500

    @app.before_request
    def require_json_writes():
        # Browsers cannot issue cross-origin JSON writes without a preflight.
        # No CORS is enabled; Next.js proxies same-origin requests.
        if request.method in {"POST", "PUT"} and not request.is_json:
            return jsonify(error="Content-Type must be application/json."), 415

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
                       credentials_required=False)

    @app.put("/api/settings")
    def put_settings():
        values = validate_options(request.get_json())
        with engine.begin() as connection:
            upsert(connection, settings, {"id": 1}, {"payload": values})
        return jsonify(values=values)

    def enqueue(kind: str, payload: dict):
        with engine.begin() as connection:
            # Serialize enqueue requests to enforce the queue cap across API workers.
            if engine.dialect.name == "postgresql":
                connection.execute(text("SELECT pg_advisory_xact_lock(716420)"))
            count = connection.scalar(select(func.count()).select_from(jobs).where(
                jobs.c.status.in_(("queued", "running"))))
            if count >= 10:
                return jsonify(error="Job queue is full. Wait for existing jobs to finish."), 429
            job_id = str(uuid4())
            carrier = {}
            inject(carrier)
            connection.execute(jobs.insert().values(
                id=job_id, kind=kind, status="queued", payload=payload, created_at=utcnow(),
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
            rows = connection.execute(select(jobs).order_by(jobs.c.created_at.desc()).limit(50)).mappings()
            return jsonify([dict(row) for row in rows])

    @app.get("/api/jobs/<job_id>")
    def get_job(job_id):
        with engine.connect() as connection:
            row = connection.execute(select(jobs).where(jobs.c.id == job_id)).mappings().first()
        if row is None:
            return jsonify(error="Job not found."), 404
        return jsonify(dict(row))

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
        click.echo("Database schema is ready (v2).")

    @app.cli.command("import-data")
    def import_data():
        """Import existing forecasts and locally available historical data."""
        from backend.tasks import import_local_data

        counts = import_local_data(engine)
        click.echo(f"Imported {counts['runs']} forecasts and {counts['seasons']} partial seasons.")

    return app
