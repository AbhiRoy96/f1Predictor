"""Execute a single claimed job in the worker's bounded subprocess."""

import logging
import sys

from opentelemetry.propagate import extract
from sqlalchemy import select

from backend.db import jobs, make_engine
from backend.tasks import execute_job
from src.observability.operations import operation
from src.observability.telemetry import configure


def main() -> int:
    configure("gridcast-worker")
    engine = make_engine()
    try:
        with engine.connect() as connection:
            job = connection.execute(select(jobs).where(
                jobs.c.id == sys.argv[1], jobs.c.status == "running")).mappings().one()
        with operation("job.execute", {"job.kind": job["kind"]},
                       context=extract(job.get("trace_context") or {})):
            run_id = execute_job(engine, dict(job))
        with engine.begin() as connection:
            connection.execute(jobs.update().where(jobs.c.id == job["id"]).values(run_id=run_id))
        return 0
    except Exception:
        logging.getLogger(__name__).exception("Job subprocess failed")
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
