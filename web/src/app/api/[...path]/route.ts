import { NextRequest, NextResponse } from "next/server";
import { backendRequest } from "@/lib/telemetry";

export const dynamic = "force-dynamic";
type Context = { params: Promise<{ path: string[] }> };

async function proxy(request: NextRequest, context: Context) {
  if (process.env.HOSTED_MODE === "true" && !request.headers.get("x-ms-client-principal-id")) {
    return NextResponse.json({ error: "Authentication required." }, { status: 401 });
  }
  const { path } = await context.params;
  if (!["health", "runs", "predictions", "jobs", "settings", "seasons"].includes(path[0])) {
    return NextResponse.json({ error: "Unknown API resource." }, { status: 404 });
  }
  if (request.method !== "GET") {
    const origin = request.headers.get("origin");
    if (origin && new URL(origin).host !== request.headers.get("host")) {
      return NextResponse.json({ error: "Cross-origin writes are not allowed." }, { status: 403 });
    }
    if (!request.headers.get("content-type")?.startsWith("application/json")) {
      return NextResponse.json({ error: "Expected application/json." }, { status: 415 });
    }
  }
  try {
    let body: string | undefined;
    if (request.method !== "GET") {
      const reader = request.body?.getReader();
      const chunks: Uint8Array[] = [];
      let size = 0;
      if (reader) {
        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          size += value.byteLength;
          if (size > 16384) {
            await reader.cancel();
            return NextResponse.json({ error: "Request body exceeds 16 KiB." }, { status: 413 });
          }
          chunks.push(value);
        }
      }
      body = Buffer.concat(chunks).toString("utf8");
    }
    const response = await backendRequest(path.map(encodeURIComponent).join("/"), {
      method: request.method,
      body,
    });
    return new NextResponse(await response.text(), {
      status: response.status, headers: {
        "Content-Type": "application/json", "Cache-Control": "no-store",
        ...(response.headers.has("retry-after") ? { "Retry-After": response.headers.get("retry-after")! } : {}),
      },
    });
  } catch (error) {
    console.error("Backend proxy failed", error);
    return NextResponse.json({ error: "Backend unavailable. Check the API container and database." }, { status: 503 });
  }
}

export { proxy as GET, proxy as POST, proxy as PUT };
