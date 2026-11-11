import "server-only";

import { context, metrics, propagation, trace, SpanStatusCode } from "@opentelemetry/api";
import { logs, SeverityNumber } from "@opentelemetry/api-logs";

export async function backendRequest(resource: string, init: RequestInit = {}): Promise<Response> {
  const operation = resource.split("/")[0];
  return trace.getTracer("gridcast.web").startActiveSpan("backend.request", async (span) => {
    const started = performance.now();
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    propagation.inject(context.active(), headers);
    let status = "failed";
    try {
      const base = process.env.BACKEND_URL ?? "http://127.0.0.1:5000";
      const response = await fetch(`${base}/api/${resource}`, {
        ...init, headers, cache: "no-store", signal: AbortSignal.timeout(20000),
      });
      span.setAttribute("http.response.status_code", response.status);
      if (response.status >= 500) span.setStatus({ code: SpanStatusCode.ERROR });
      status = response.ok ? "succeeded" : "failed";
      return response;
    } catch (error) {
      span.setStatus({ code: SpanStatusCode.ERROR });
      if (error instanceof Error) span.recordException(error);
      throw error;
    } finally {
      const duration = (performance.now() - started) / 1000;
      const attributes = { operation, status };
      metrics.getMeter("gridcast.web").createHistogram("gridcast.web.backend.duration", { unit: "s" }).record(duration, attributes);
      logs.getLogger("gridcast.web").emit({
        severityNumber: status === "failed" ? SeverityNumber.ERROR : SeverityNumber.INFO,
        severityText: status === "failed" ? "ERROR" : "INFO",
        body: "Backend request completed",
        attributes: { ...attributes, duration_seconds: duration },
      });
      span.end();
    }
  });
}
