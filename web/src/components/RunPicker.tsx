"use client";

import { usePathname, useRouter } from "next/navigation";

import type { ManifestEntry } from "@/lib/types";

export function RunPicker({ manifest, current }: { manifest: ManifestEntry[]; current: string }) {
  const router = useRouter();
  const pathname = usePathname();
  if (manifest.length < 2) return null;
  return (
    <label className="flex items-center gap-3 text-xs text-fog">
      <span className="eyebrow">Saved run</span>
      <select
        value={current}
        onChange={(event) => router.push(`${pathname}?run=${encodeURIComponent(event.target.value)}`)}
        className="rounded-md border border-line bg-asphalt px-3 py-2 text-sm text-paper"
      >
        {manifest.map((entry) => (
          <option key={entry.id} value={entry.id}>
            {entry.demo ? "Illustrative sample" : `${entry.year ?? ""} ${entry.event_name ?? entry.id}`.trim()}
            {entry.stage ? ` (${entry.stage})` : ""}
          </option>
        ))}
      </select>
    </label>
  );
}
