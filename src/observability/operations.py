"""Low-cardinality operation metrics, ML cost estimates and correlated spans."""

import inspect
import logging
import math
import os
import time
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from functools import wraps

from opentelemetry import metrics, trace

logger = logging.getLogger(__name__)
_billing_depth = ContextVar("ml_billing_depth", default=0)
_profiling = False


def compute_rate() -> float | None:
    value = os.getenv("ML_COMPUTE_HOURLY_USD", "").strip()
    if not value:
        return None
    rate = float(value)
    if not math.isfinite(rate) or rate < 0:
        raise ValueError("ML_COMPUTE_HOURLY_USD must be finite and nonnegative.")
    return rate


def profile_tags(attributes: dict):
    if not _profiling:
        return nullcontext()
    import pyroscope

    return pyroscope.tag_wrapper({key.replace(".", "_"): str(value)
                                  for key, value in attributes.items()})


@contextmanager
def operation(name: str, attributes: dict | None = None, *, billable: bool = False,
              context=None):
    """Record outcomes and cost without suppressing business exceptions."""
    attributes = attributes or {}
    labels = {"operation": name, **{key: attributes[key] for key in ("ml.model", "job.kind")
                                    if key in attributes}}
    meter = metrics.get_meter("gridcast.operations")
    started, cpu_started = time.perf_counter(), time.process_time()
    status = "succeeded"
    depth = _billing_depth.get()
    rate = compute_rate() if billable and depth == 0 else None
    token = _billing_depth.set(depth + 1) if billable else None
    with trace.get_tracer("gridcast.operations").start_as_current_span(
            name, attributes=attributes, context=context) as span:
        logger.info("Operation started", extra={"telemetry": {"operation": name, **attributes}})
        try:
            with profile_tags(labels):
                yield span
        except Exception:
            status = "failed"
            logger.exception("Operation failed", extra={"telemetry": {"operation": name, **attributes}})
            raise
        finally:
            elapsed = time.perf_counter() - started
            cpu = time.process_time() - cpu_started
            labels["status"] = status
            meter.create_histogram("gridcast.operation.duration", unit="s").record(elapsed, labels)
            meter.create_counter("gridcast.operation.count").add(1, labels)
            fields = {"operation": name, **attributes, "status": status,
                      "duration_seconds": elapsed, "cpu_seconds": cpu}
            if billable and depth == 0:
                meter.create_counter("gridcast.ml.cpu_time", unit="s").add(cpu, labels)
                span.set_attribute("ml.cost.configured", rate is not None)
                if rate is not None:
                    cost = elapsed * rate / 3600
                    meter.create_counter("gridcast.ml.estimated_cost").add(cost, labels)
                    span.set_attribute("ml.estimated_cost_usd", cost)
                    fields["estimated_cost_usd"] = cost
                else:
                    fields["estimated_cost_usd"] = None
            if token is not None:
                _billing_depth.reset(token)
            logger.info("Operation completed", extra={"telemetry": fields})


def observe(name: str, *, billable: bool = False):
    """Decorate existing model/pipeline functions without changing their signatures."""
    def decorate(function):
        signature = inspect.signature(function)

        @wraps(function)
        def wrapped(*args, **kwargs):
            arguments = signature.bind(*args, **kwargs).arguments
            owner = arguments.get("self")
            model = arguments.get("strength_model", getattr(owner, "strength_model", None))
            attributes = {"ml.model": str(model)} if model is not None else {}
            for key in ("session", "stage", "year", "n_simulations", "seed"):
                if key in arguments:
                    attributes[f"ml.{key}"] = arguments[key]
            frame = arguments.get("df", arguments.get("frame"))
            if frame is not None and hasattr(frame, "shape"):
                attributes["ml.rows"] = int(frame.shape[0])
                attributes["ml.columns"] = int(frame.shape[1])
            with operation(name, attributes, billable=billable):
                return function(*args, **kwargs)
        return wrapped
    return decorate


def record_evaluation(summary) -> None:
    meter = metrics.get_meter("gridcast.operations")
    names = ("win_log_loss", "podium_brier", "top_3_overlap", "spearman",
             "position_mae", "dnf_brier_skill")
    for row in summary.to_dict(orient="records"):
        for name in names:
            value = row.get(name)
            if value is not None and math.isfinite(float(value)):
                meter.create_histogram(f"gridcast.ml.quality.{name}").record(
                    float(value), {"ml.model": str(row["model"])})
        logger.info("Model evaluation", extra={"telemetry": {
            "operation": "ml.evaluate", "ml.model": str(row["model"]),
            **{name: row[name] for name in names if name in row},
        }})
