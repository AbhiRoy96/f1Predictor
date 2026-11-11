"""Opt-in OTLP bootstrap, safe database instrumentation and CPU profiling."""

import atexit
import logging
import os
import platform
import time
from uuid import uuid4

from opentelemetry import metrics, trace

from src.observability import operations
from src.observability.runtime import RuntimeMetrics
from src.utils.logging import JsonFormatter, setup_logger

_initialized = False
_providers = []
logger = setup_logger(__name__)


def configure(service_name: str) -> bool:
    global _initialized
    operations.compute_rate()
    if os.getenv("OTEL_ENABLED", "false").lower() != "true":
        return False
    if os.getenv("HOSTED_MODE") == "true" and not os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
        raise RuntimeError("Hosted telemetry requires an explicit controlled OTLP endpoint.")
    if _initialized:
        return True
    from opentelemetry._logs import set_logger_provider
    from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.requests import RequestsInstrumentor
    from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = Resource.create({
        "service.name": os.getenv("OTEL_SERVICE_NAME", service_name),
        "service.namespace": "gridcast", "service.instance.id": str(uuid4()),
        "deployment.environment.name": os.getenv("DEPLOYMENT_ENVIRONMENT", "local"),
    })
    tracer = TracerProvider(resource=resource, shutdown_on_exit=False)
    tracer.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(tracer)
    meter = MeterProvider(resource=resource, shutdown_on_exit=False, metric_readers=[PeriodicExportingMetricReader(
        OTLPMetricExporter(), export_interval_millis=10000)])
    metrics.set_meter_provider(meter)
    logs = LoggerProvider(resource=resource, shutdown_on_exit=False)
    logs.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
    set_logger_provider(logs)
    handler = LoggingHandler(level=logging.INFO, logger_provider=logs)
    handler.setFormatter(JsonFormatter())
    logging.getLogger().addHandler(handler)
    RequestsInstrumentor().instrument(excluded_urls=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://lgtm:4318"))
    RuntimeMetrics(meter.get_meter("gridcast.runtime"),
                   gpu_enabled=os.getenv("GPU_METRICS_ENABLED", "false").lower() == "true")
    if os.getenv("PYROSCOPE_ENABLED", "false").lower() == "true":
        if platform.system() != "Linux":
            raise RuntimeError("CPU profiling requires Linux. Use the backend containers.")
        import pyroscope

        pyroscope.configure(application_name=resource.attributes["service.name"],
                            server_address=os.environ["PYROSCOPE_SERVER_ADDRESS"],
                            sample_rate=100, oncpu=True, gil_only=False,
                            tags={"service_name": resource.attributes["service.name"]})
        operations._profiling = True
    _providers.extend([tracer, meter, logs])
    _initialized = True
    atexit.register(shutdown)
    logger.info("Observability initialized", extra={"telemetry": {
        "service": resource.attributes["service.name"],
        "profiling_enabled": operations._profiling,
        "cost_rate_configured": operations.compute_rate() is not None,
    }})
    return True


def shutdown() -> None:
    while _providers:
        provider = _providers.pop(0)
        provider.force_flush(timeout_millis=5000)
        provider.shutdown()


def instrument_database(engine) -> None:
    if not _initialized:
        return
    from sqlalchemy import event

    meter = metrics.get_meter("gridcast.database")
    duration = meter.create_histogram("gridcast.db.query.duration", unit="s")
    count = meter.create_counter("gridcast.db.query.count")

    def record(context, status):
        if context is not None and hasattr(context, "_gridcast_started"):
            labels = {"db.operation": context._gridcast_operation, "status": status}
            duration.record(time.perf_counter() - context._gridcast_started, labels)
            count.add(1, labels)
            if status == "failed":
                context._gridcast_span.set_status(trace.StatusCode.ERROR)
            context._gridcast_span.end()

    @event.listens_for(engine, "before_cursor_execute")
    def before(connection, cursor, statement, parameters, context, executemany):
        context._gridcast_started = time.perf_counter()
        verb = statement.lstrip().split(None, 1)[0].upper() if statement.strip() else "OTHER"
        context._gridcast_operation = verb if verb in {"SELECT", "INSERT", "UPDATE", "DELETE"} else "OTHER"
        context._gridcast_span = trace.get_tracer("gridcast.database").start_span(
            f"db.{context._gridcast_operation}", kind=trace.SpanKind.CLIENT,
            attributes={"db.system": engine.dialect.name,
                        "db.operation.name": context._gridcast_operation})

    @event.listens_for(engine, "after_cursor_execute")
    def after(connection, cursor, statement, parameters, context, executemany):
        record(context, "succeeded")

    @event.listens_for(engine, "handle_error")
    def failed(error):
        record(error.execution_context, "failed")
