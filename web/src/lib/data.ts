import "server-only";

import { promises as fs } from "node:fs";
import path from "node:path";

import type { ManifestEntry, Run } from "./types";

const DATA_DIR = path.join(process.cwd(), "public", "data");
const SAFE_ID = /^[A-Za-z0-9_.-]+$/;

export async function getManifest(): Promise<ManifestEntry[]> {
  try {
    return JSON.parse(await fs.readFile(path.join(DATA_DIR, "manifest.json"), "utf-8")) as ManifestEntry[];
  } catch {
    return [];
  }
}

// Prefers an explicit run, then the newest saved forecast, then the labelled sample.
export async function getRun(requested?: string): Promise<{ run: Run | null; manifest: ManifestEntry[] }> {
  const manifest = await getManifest();
  const preferred = requested && SAFE_ID.test(requested) && manifest.some((m) => m.id === requested)
    ? requested
    : (manifest.find((m) => !m.demo) ?? manifest[0])?.id;
  if (!preferred) return { run: null, manifest };
  try {
    const raw = await fs.readFile(path.join(DATA_DIR, "runs", `${preferred}.json`), "utf-8");
    return { run: JSON.parse(raw) as Run, manifest };
  } catch {
    return { run: null, manifest };
  }
}
