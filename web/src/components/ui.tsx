import type { ReactNode } from "react";

import { teamColor } from "@/lib/format";

export function Section({ eyebrow, title, action, children }: {
  eyebrow?: string; title: string; action?: ReactNode; children: ReactNode;
}) {
  return (
    <section className="mt-12">
      <div className="mb-5 flex items-end justify-between gap-4">
        <div>
          {eyebrow && <p className="eyebrow mb-1 text-red">{eyebrow}</p>}
          <h2 className="display text-2xl sm:text-3xl">{title}</h2>
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}

export function Card({ children, className = "", accent }: {
  children: ReactNode; className?: string; accent?: string;
}) {
  return (
    <div
      className={`relative overflow-hidden rounded-xl border border-line bg-asphalt ${className}`}
      style={accent ? { borderTop: `3px solid ${accent}` } : undefined}
    >
      {children}
    </div>
  );
}

export function Stat({ label, value, hint, tone }: {
  label: string; value: ReactNode; hint?: string; tone?: "red" | "teal" | "amber";
}) {
  const color = tone === "teal" ? "text-teal" : tone === "amber" ? "text-amber" : "text-paper";
  return (
    <Card className="p-5">
      <p className="eyebrow">{label}</p>
      <p className={`display mt-2 text-3xl ${color}`}>{value}</p>
      {hint && <p className="mt-1 text-xs text-fog">{hint}</p>}
    </Card>
  );
}

export function Pill({ children, tone = "neutral" }: { children: ReactNode; tone?: "neutral" | "red" | "amber" | "teal" }) {
  const styles = {
    neutral: "border-line text-fog",
    red: "border-red/60 bg-red/10 text-red",
    amber: "border-amber/60 bg-amber/10 text-amber",
    teal: "border-teal/60 bg-teal/10 text-teal",
  }[tone];
  return (
    <span className={`inline-flex items-center rounded-full border px-3 py-1 text-[0.68rem] font-bold uppercase tracking-wider ${styles}`}>
      {children}
    </span>
  );
}

export function TeamTag({ team }: { team?: string | null }) {
  return (
    <span className="inline-flex items-center gap-2 text-xs text-fog">
      <span className="h-3 w-1 rounded-sm" style={{ background: teamColor(team) }} />
      {team ?? "Unknown"}
    </span>
  );
}

export function ProbBar({ value, max = 1, color = "#e10600", label }: {
  value: number; max?: number; color?: string; label?: string;
}) {
  const width = Math.max(0, Math.min(100, (value / max) * 100));
  return (
    <div className="h-2 w-full overflow-hidden rounded-full bg-kerb" role="img" aria-label={label}>
      <div className="h-full rounded-full" style={{ width: `${width}%`, background: color }} />
    </div>
  );
}
