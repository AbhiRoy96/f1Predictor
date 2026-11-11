import { NextRequest, NextResponse } from "next/server";
import { backendRequest } from "@/lib/telemetry";

export const dynamic = "force-dynamic";
type Context = { params: Promise<{ path: string[] }> };

async function proxy(request: NextRequest, context: Context) {
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
    const response = await backendRequest(path.map(encodeURIComponent).join("/"), {
      method: request.method,
      body: request.method === "GET" ? undefined : await request.text(),
    });
    return new NextResponse(await response.text(), {
      status: response.status, headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
    });
  } catch (error) {
    console.error("Backend proxy failed", error);
    return NextResponse.json({ error: "Backend unavailable. Check the API container and database." }, { status: 503 });
  }
}

export { proxy as GET, proxy as POST, proxy as PUT };
