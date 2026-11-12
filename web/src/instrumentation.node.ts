import { OTLPLogExporter } from "@opentelemetry/exporter-logs-otlp-http";
import { OTLPMetricExporter } from "@opentelemetry/exporter-metrics-otlp-http";
import { OTLPTraceExporter } from "@opentelemetry/exporter-trace-otlp-http";
import { BatchLogRecordProcessor } from "@opentelemetry/sdk-logs";
import { PeriodicExportingMetricReader } from "@opentelemetry/sdk-metrics";
import { NodeSDK } from "@opentelemetry/sdk-node";

let sdk: NodeSDK | undefined;

export function registerNodeTelemetry() {
  if (sdk) return;
  if (process.env.HOSTED_MODE === "true" && !process.env.OTEL_EXPORTER_OTLP_ENDPOINT) {
    throw new Error("Hosted telemetry requires an explicit controlled OTLP endpoint.");
  }
  sdk = new NodeSDK({
    serviceName: process.env.OTEL_SERVICE_NAME ?? "gridcast-web",
    traceExporter: new OTLPTraceExporter(),
    metricReaders: [new PeriodicExportingMetricReader({
      exporter: new OTLPMetricExporter(), exportIntervalMillis: 10000,
    })],
    logRecordProcessors: [new BatchLogRecordProcessor({ exporter: new OTLPLogExporter() })],
  });
  sdk.start();
  process.once("SIGTERM", () => { void sdk?.shutdown(); });
}
