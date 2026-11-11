import type { RunMeta } from "@/lib/types";

export function DataNotice({ meta }: { meta: RunMeta }) {
  const limited = (meta.training_races ?? 0) < 30;
  return (
    <div className="space-y-2" role="status">
      <dl className="flex flex-wrap gap-x-6 gap-y-2 rounded-lg border border-line bg-asphalt px-4 py-3 text-xs">
        {[
          ["Forecast stage", meta.stage?.replaceAll("_", " ") ?? "Not recorded"],
          ["History coverage", meta.history_scope?.replaceAll("_", " ") ?? "Not recorded"],
          ["Held-out calibration races", meta.calibration_races ?? "Not recorded"],
          ["Model version", meta.model_version ?? "Not recorded"],
          ["Training end (UTC)", meta.training_end_utc ?? "Not recorded"],
          ["Publication phase", meta.forecast_phase?.replaceAll("_", " ") ?? "Manual"],
          ["Source published (UTC)", meta.source_published_at_utc ?? "Not recorded"],
        ].map(([label, value]) => (
          <div key={label}>
            <dt className="text-fog">{label}</dt>
            <dd className="mt-1 text-paper">{value}</dd>
          </div>
        ))}
      </dl>
      <p className="text-xs text-fog">
        Experimental model output, not a guarantee or betting advice. Outcome ranges do not include every source
        of model uncertainty. Calibration sample size counts chronological out-of-fold races, not training races.
      </p>
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
