"""Contracts for correlated telemetry, additive migrations and honest resource estimates."""

import json
import logging
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from opentelemetry import trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import select, text

from backend.app import create_app
from backend.db import jobs, migrate, schema_versions
from backend.worker import process_next, queue_depth
from src.observability import telemetry
from src.observability.operations import compute_rate, operation
from src.observability.runtime import RuntimeMetrics
from src.utils.logging import JsonFormatter


class ObservabilityTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {
            "OTEL_ENABLED": "false", "PYROSCOPE_ENABLED": "false",
            "ML_COMPUTE_HOURLY_USD": "",
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.exporter = InMemorySpanExporter()
        self.traces = TracerProvider()
        self.traces.add_span_processor(SimpleSpanProcessor(self.exporter))
        self.tracer = self.traces.get_tracer("test")
        self.reader = InMemoryMetricReader()
        self.meters = MeterProvider(metric_readers=[self.reader])
        self.addCleanup(self.traces.shutdown)
        self.addCleanup(self.meters.shutdown)
        for target, value in (("trace.get_tracer", self.tracer),
                              ("metrics.get_meter", self.meters.get_meter("test"))):
            replacement = patch(f"src.observability.operations.{target}", return_value=value)
            replacement.start()
            self.addCleanup(replacement.stop)

    def metric_points(self, name):
        data = self.reader.get_metrics_data()
        if data is None:
            return []
        return [point for resource in data.resource_metrics for scope in resource.scope_metrics
                for metric in scope.metrics if metric.name == name for point in metric.data.data_points]

    def make_app(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        app = create_app({"TESTING": True, "DATABASE_URL":
                          "sqlite:///" + str(Path(directory.name) / "telemetry.db")})
        engine = app.extensions["db"]
        self.addCleanup(engine.dispose)
        migrate(engine)
        return app, engine

    def test_unset_rate_is_unavailable_not_zero_and_logs_correlate(self):
        with operation("ml.train", {"ml.model": "ensemble"}, billable=True) as span:
            record = logging.LogRecord("model", logging.INFO, "", 1, "Training", (), None)
            payload = json.loads(JsonFormatter().format(record))
            self.assertEqual(payload["trace_id"], format(span.get_span_context().trace_id, "032x"))
            self.assertEqual(payload["span_id"], format(span.get_span_context().span_id, "016x"))
        self.assertEqual(self.metric_points("gridcast.ml.estimated_cost"), [])
        self.assertFalse(self.exporter.get_finished_spans()[0].attributes["ml.cost.configured"])

    def test_nested_billable_operations_are_not_double_counted(self):
        with patch.dict(os.environ, {"ML_COMPUTE_HOURLY_USD": "3600"}), \
                patch("src.observability.operations.time.perf_counter", side_effect=[0, 1, 2, 3]):
            with operation("ml.train", billable=True):
                with operation("ml.component", billable=True):
                    pass
        points = self.metric_points("gridcast.ml.estimated_cost")
        self.assertEqual(len(points), 1)
        self.assertAlmostEqual(points[0].value, 3)
        self.assertEqual(points[0].attributes["operation"], "ml.train")

    def test_failed_operation_preserves_exception_status_and_billing_depth(self):
        with patch.dict(os.environ, {"ML_COMPUTE_HOURLY_USD": "1"}):
            with self.assertRaisesRegex(ValueError, "training failed"):
                with operation("ml.train", billable=True):
                    raise ValueError("training failed")
            with operation("ml.predict", billable=True):
                pass
        failed, succeeded = self.exporter.get_finished_spans()
        self.assertEqual(failed.status.status_code, trace.StatusCode.ERROR)
        self.assertTrue(any(event.name == "exception" for event in failed.events))
        self.assertTrue(succeeded.attributes["ml.cost.configured"])
        self.assertEqual({point.attributes["status"]
                          for point in self.metric_points("gridcast.ml.estimated_cost")},
                         {"failed", "succeeded"})

    def test_rate_rejects_nonfinite_negative_and_invalid_values(self):
        for value in ("nan", "inf", "-1", "invalid"):
            with self.subTest(value=value), patch.dict(os.environ, {"ML_COMPUTE_HOURLY_USD": value}):
                with self.assertRaises(ValueError):
                    compute_rate()
        with patch.dict(os.environ, {"ML_COMPUTE_HOURLY_USD": "0"}):
            self.assertEqual(compute_rate(), 0)

    def test_hosted_telemetry_requires_an_explicit_endpoint(self):
        with patch.dict(os.environ, {"HOSTED_MODE": "true", "OTEL_ENABLED": "true"}):
            os.environ.pop("OTEL_EXPORTER_OTLP_ENDPOINT", None)
            with self.assertRaisesRegex(RuntimeError, "explicit controlled OTLP endpoint"):
                telemetry.configure("gridcast-api")

    def test_query_spans_have_outcomes_but_not_query_text_or_parameters(self):
        _, engine = self.make_app()
        with patch.object(telemetry, "_initialized", True):
            telemetry.instrument_database(engine)
        with self.tracer.start_as_current_span("request") as parent:
            with engine.connect() as connection:
                self.assertEqual(connection.scalar(text("SELECT :private"), {"private": "do-not-export"}),
                                 "do-not-export")
                with self.assertRaises(Exception):
                    connection.execute(text("SELECT * FROM missing_table"))
        spans = self.exporter.get_finished_spans()
        queries = [span for span in spans if span.name == "db.SELECT"]
        self.assertEqual(len(queries), 2)
        self.assertTrue(all(span.parent.span_id == parent.get_span_context().span_id for span in queries))
        self.assertEqual(queries[1].status.status_code, trace.StatusCode.ERROR)
        self.assertNotIn("do-not-export", str([span.attributes for span in queries]))
        self.assertTrue(all("db.statement" not in span.attributes for span in queries))
        self.assertEqual({point.attributes["status"]
                          for point in self.metric_points("gridcast.db.query.count")},
                         {"succeeded", "failed"})

    def test_queue_trace_survives_the_database_boundary(self):
        app, engine = self.make_app()
        with self.tracer.start_as_current_span("request") as parent:
            response = app.test_client().post("/api/seasons/2024/sync", json={})
        self.assertEqual(response.status_code, 202)
        with engine.connect() as connection:
            carrier = connection.scalar(select(jobs.c.trace_context))
        self.assertEqual(set(carrier), {"traceparent"})
        with patch("backend.worker.execute_job", return_value=None):
            self.assertTrue(process_next(engine))
        worker = next(span for span in self.exporter.get_finished_spans() if span.name == "job.process")
        self.assertEqual(worker.context.trace_id, parent.get_span_context().trace_id)
        self.assertEqual(worker.parent.span_id, parent.get_span_context().span_id)

    def test_queue_metrics_reset_to_zero_when_empty(self):
        app, engine = self.make_app()
        self.assertEqual(len(queue_depth(engine)), 4)
        self.assertTrue(all(point.value == 0 for point in queue_depth(engine)))
        app.test_client().post("/api/seasons/2024/sync", json={})
        self.assertEqual(sum(point.value for point in queue_depth(engine)), 1)
        with patch("backend.worker.execute_job", return_value=None):
            process_next(engine)
        self.assertTrue(all(point.value == 0 for point in queue_depth(engine)))

    def test_v1_migration_preserves_existing_jobs(self):
        app, engine = self.make_app()
        job_id = app.test_client().post("/api/seasons/2024/sync", json={}).json["id"]
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE jobs DROP COLUMN trace_context"))
            connection.execute(schema_versions.delete())
            connection.execute(schema_versions.insert().values(version=1))
        migrate(engine)
        migrate(engine)
        with engine.connect() as connection:
            job = connection.execute(select(jobs).where(jobs.c.id == job_id)).mappings().one()
            self.assertEqual(job["payload"], {"year": 2024})
            self.assertEqual(job["status"], "queued")
            self.assertIsNone(job["trace_context"])
            self.assertEqual(connection.scalars(select(schema_versions.c.version)).all(), [1, 2, 3, 4])

    def test_disabled_gpu_has_no_invented_measurements(self):
        runtime = RuntimeMetrics(Mock())
        self.assertEqual(list(runtime.gpu_callback("utilization")(None)), [])
        self.assertGreater(runtime.memory(None)[0].value, 0)

    def test_nvidia_measurements_use_documented_units(self):
        nvml = Mock()
        nvml.nvmlDeviceGetCount.return_value = 1
        nvml.nvmlDeviceGetUtilizationRates.return_value = SimpleNamespace(gpu=25)
        nvml.nvmlDeviceGetMemoryInfo.return_value = SimpleNamespace(used=4096)
        nvml.nvmlDeviceGetPowerUsage.return_value = 125000
        nvml.nvmlDeviceGetTemperature.return_value = 60
        with patch.dict("sys.modules", {"pynvml": nvml}):
            runtime = RuntimeMetrics(Mock(), gpu_enabled=True)
        for field, expected in (("utilization", .25), ("memory", 4096),
                                ("power", 125), ("temperature", 60)):
            with self.subTest(field=field):
                point = list(runtime.gpu_callback(field)(None))[0]
                self.assertEqual(point.value, expected)
                self.assertEqual(point.attributes, {"gpu.index": "0"})

    def test_unavailable_gpu_is_logged_and_not_reported_as_idle(self):
        class NvmlError(Exception):
            pass

        nvml = Mock(NVMLError=NvmlError)
        nvml.nvmlInit.side_effect = NvmlError("driver unavailable")
        with patch.dict("sys.modules", {"pynvml": nvml}), \
                self.assertLogs("src.observability.runtime", level="WARNING") as logs:
            runtime = RuntimeMetrics(Mock(), gpu_enabled=True)
        self.assertIn("driver unavailable", logs.output[0])
        self.assertEqual(list(runtime.gpu_callback("memory")(None)), [])

    def test_font_uses_same_origin_delivery_and_method_is_removed(self):
        root = Path(__file__).parents[2] / "web"
        css = (root / "src" / "app" / "globals.css").read_text(encoding="utf-8")
        config = (root / "next.config.mjs").read_text(encoding="utf-8")
        self.assertNotIn("Formula1-Bold", css)
        self.assertIn('"Noto Sans Variable"', css)
        self.assertIn('font-style: normal', css)
        self.assertNotIn('font-style: italic', css)
        self.assertNotIn("Formula1-Bold", config)
        self.assertNotIn("formula1.com", config)
        self.assertFalse((root / "src" / "app" / "method" / "page.tsx").exists())


if __name__ == "__main__":
    unittest.main()
