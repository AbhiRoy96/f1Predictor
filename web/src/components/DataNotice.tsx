import type { RunMeta } from "@/lib/types";

export function DataNotice({ meta }: { meta: RunMeta }) {
  const limited = (meta.training_races ?? 0) < 30;
  return (
    <div className="space-y-2" role="status">
      {meta.demo && (
        <div className="rounded-lg border border-amber/70 bg-amber/10 px-4 py-3 text-sm text-amber">
          <strong className="uppercase tracking-wide">Illustrative sample.</strong> These numbers were generated from
          invented driver strengths to demonstrate the interface. They are not a forecast of any real race. Run{" "}
          <code className="rounded bg-track px-1">python main.py race 2026 &quot;Brazilian Grand Prix&quot;</code> then{" "}
          <code className="rounded bg-track px-1">npm run export-data</code> to display a real saved forecast.
        </div>
      )}
      {!meta.demo && limited && (
        <div className="rounded-lg border border-amber/70 bg-amber/10 px-4 py-3 text-sm text-amber">
          <strong className="uppercase tracking-wide">Limited evidence.</strong> This model saw fewer than 30 training
          races. Probabilities and calibration need more held-out races before they can be trusted.
        </div>
      )}
      {meta.history_scope === "available_collected_sessions_not_full_championship" && (
        <p className="text-xs text-fog">
          History covers collected events only. Season points and form are sampled-history summaries, not complete
          championship standings.
        </p>
      )}
    </div>
  );
}
