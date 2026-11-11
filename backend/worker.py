"""Serialized, crash-visible database queue worker: python -m backend.worker."""

import logging
import time
from datetime import timezone

from sqlalchemy import func, select, text
from opentelemetry import metrics
from opentelemetry.metrics import Observation
from opentelemetry.propagate import extract

from backend.db import jobs, make_engine, utcnow
from backend.tasks import execute_job
from src.observability.operations import operation
from src.observability.telemetry import configure

logger = logging.getLogger(__name__)


def queue_depth(engine) -> list[Observation]:
    with engine.connect() as connection:
        rows = connection.execute(select(jobs.c.kind, jobs.c.status, func.count().label("job_count"))
                                  .where(jobs.c.status.in_(("queued", "running")))
                                  .group_by(jobs.c.kind, jobs.c.status))
        counts = {(row.kind, row.status): row.job_count for row in rows}
    return [Observation(counts.get((kind, status), 0), {"job.kind": kind, "status": status})
            for kind in ("prediction", "season_sync") for status in ("queued", "running")]


def process_next(engine) -> bool:
    with engine.begin() as connection:
        job = connection.execute(select(jobs).where(jobs.c.status == "queued")
                                 .order_by(jobs.c.created_at).limit(1)
                                 .with_for_update(skip_locked=True)).mappings().first()
        if job is None:
            return False
        job = dict(job)
        connection.execute(jobs.update().where(jobs.c.id == job["id"]).values(
            status="running", started_at=utcnow()))
    created = job["created_at"]
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    meter = metrics.get_meter("gridcast.queue")
    meter.create_histogram("gridcast.jobs.queue_wait", unit="s").record(
        max(0, (utcnow() - created).total_seconds()), {"job.kind": job["kind"]})
    try:
        with operation("job.process", {"job.id": job["id"], "job.kind": job["kind"]},
                       context=extract(job.get("trace_context") or {})):
            run_id = execute_job(engine, job)
            with engine.begin() as connection:
                connection.execute(jobs.update().where(jobs.c.id == job["id"]).values(
                    status="succeeded", run_id=run_id, finished_at=utcnow()))
    except Exception as error:
        logger.exception("Job %s failed", job["id"])
        with engine.begin() as connection:
            connection.execute(jobs.update().where(jobs.c.id == job["id"]).values(
                status="failed", error=str(error)[:4000], finished_at=utcnow()))
    return True


def main() -> int:
    configure("gridcast-worker")
    engine = make_engine()
    metrics.get_meter("gridcast.queue").create_observable_gauge(
        "gridcast.jobs.active", callbacks=[lambda _: queue_depth(engine)])
    # The existing pipeline writes shared Parquet/model files. One worker owns them,
    # including when multiple worker containers are accidentally started.
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as lock:
        if not lock.scalar(text("SELECT pg_try_advisory_lock(716421)")):
            logger.error("Another prediction worker is already running.")
            return 2
        with engine.begin() as connection:
            connection.execute(jobs.update().where(jobs.c.status == "running").values(
                status="failed", error="Worker stopped before completion. Submit the job again.",
                finished_at=utcnow()))
        logger.info("Worker ready")
        while True:
            lock.execute(text("SELECT 1"))
            if not process_next(engine):
                time.sleep(2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
