export async function register() {
  if (process.env.NEXT_RUNTIME === "nodejs" && process.env.OTEL_ENABLED === "true") {
    const { registerNodeTelemetry } = await import("./instrumentation.node");
    registerNodeTelemetry();
  }
}
