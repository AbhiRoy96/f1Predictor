import "server-only";

import type { ManifestEntry, Run } from "./types";
import { backendRequest } from "./telemetry";

export async function backendGet<T>(resource: string): Promise<T> {
  const response = await backendRequest(resource);
  if (!response.ok) throw new Error(`Backend request failed (${response.status}): ${resource}`);
  return response.json() as Promise<T>;
}

export async function getManifest(): Promise<ManifestEntry[]> {
  return backendGet<ManifestEntry[]>("runs");
}

export async function getRun(requested?: string): Promise<{ run: Run | null; manifest: ManifestEntry[] }> {
  const manifest = await getManifest();
  if (requested && !manifest.some((entry) => entry.id === requested)) {
    throw new Error("Requested forecast was not found.");
  }
  const preferred = requested ?? manifest.find((entry) => !entry.demo)?.id;
  if (!preferred) return { run: null, manifest };
  return { run: await backendGet<Run>(`runs/${encodeURIComponent(preferred)}`), manifest };
}
