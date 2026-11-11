import { EmptyState, PageHeader, loadRun, type PageProps } from "@/components/PageHeader";
import { ReliabilityChart } from "@/components/ReliabilityChart";
import { Card, Pill, Section } from "@/components/ui";

// Measured on 39 held-out races (stage post_quali) before the Phase 1 correctness fixes.
const BEFORE_FIXES = [
  { model: "Race model", win: 1.77, podium: 0.079, overlap: 1.79, spearman: 0.63, mae: 3.42, note: "" },
  { model: "Championship baseline", win: 2.2, podium: 0.096, overlap: 1.41, spearman: 0.51, mae: 4.12, note: "" },
  { model: "Team baseline", win: 2.35, podium: 0.101, overlap: 1.38, spearman: 0.52, mae: 4.15, note: "" },
  { model: "Grid baseline", win: 2.97, podium: 0.124, overlap: 1.18, spearman: 0.28, mae: 4.97, note: "Degenerate: every driver had grid 10.5. Fixed since; re-run needed." },
];

const best = (key: "win" | "podium" | "overlap" | "spearman" | "mae", low: boolean) => {
  const values = BEFORE_FIXES.slice(0, 3).map((row) => row[key]);
  return low ? Math.min(...values) : Math.max(...values);
};

export default async function LabPage(props: PageProps) {
  const { run, manifest } = await loadRun(props);
  const summary = run?.reports.summary ?? [];
  const reliability = (run?.reports.reliability ?? []).filter((r) => r.model === "race_model" && r.target === "win");

  return (
    <>
      <PageHeader
        eyebrow="Model Lab"
        title="Does it beat the obvious?"
        subtitle="A model is only useful if it beats simple baselines on races it has never seen. This page shows measured walk-forward results, never invented ones."
        run={run ?? undefined}
        manifest={manifest}
      />

      <Section eyebrow="Measured · before fixes" title="Walk-forward scoreboard">
        <div className="mb-4 flex flex-wrap gap-2">
          <Pill tone="amber">39 held-out races</Pill>
          <Pill>Stage: post-qualifying</Pill>
          <Pill>Run on 2026-11-11</Pill>
          <Pill tone="red">Before grid and DNF fixes</Pill>
        </div>
        <div className="overflow-x-auto rounded-xl border border-line">
          <table className="w-full min-w-[720px] text-left text-sm">
            <caption className="sr-only">Walk-forward results for the race model and three baselines</caption>
            <thead className="bg-kerb text-[0.7rem] uppercase tracking-widest text-fog">
              <tr>
                <th className="px-4 py-3">Model</th>
                <th className="px-4 py-3">Win log loss ↓</th>
                <th className="px-4 py-3">Podium Brier ↓</th>
                <th className="px-4 py-3">Top-3 overlap ↑</th>
                <th className="px-4 py-3">Spearman ↑</th>
                <th className="px-4 py-3">Position MAE ↓</th>
              </tr>
            </thead>
            <tbody>
              {BEFORE_FIXES.map((row, i) => {
                const ok = i < 3;
                const cell = (value: number, key: "win" | "podium" | "overlap" | "spearman" | "mae", low: boolean, digits: number) => (
                  <td className={`px-4 py-3 tabular-nums ${ok && value === best(key, low) ? "font-bold text-teal" : ""}`}>{value.toFixed(digits)}</td>
                );
                return (
                  <tr key={row.model} className={`border-t border-line ${i === 0 ? "bg-red/10" : "bg-asphalt"}`}>
                    <th scope="row" className="px-4 py-3 font-bold">
                      {row.model}
                      {row.note && <span className="mt-1 block text-xs font-normal text-amber">{row.note}</span>}
                    </th>
                    {cell(row.win, "win", true, 2)}
                    {cell(row.podium, "podium", true, 3)}
                    {cell(row.overlap, "overlap", false, 2)}
                    {cell(row.spearman, "spearman", false, 2)}
                    {cell(row.mae, "mae", true, 2)}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <div className="mt-5 grid gap-4 md:grid-cols-3">
          <Card className="p-5">
            <p className="eyebrow text-teal">What looks good</p>
            <p className="mt-2 text-sm text-fog">The race model leads every metric against the working baselines, and podium and Spearman gaps look clear.</p>
          </Card>
          <Card className="p-5">
            <p className="eyebrow text-amber">What is not proven</p>
            <p className="mt-2 text-sm text-fog">Win log loss intervals overlap the championship baseline ([1.45, 2.13] against [1.74, 2.56]), so that win is not yet established.</p>
          </Card>
          <Card className="p-5">
            <p className="eyebrow text-red">Known weakness</p>
            <p className="mt-2 text-sm text-fog">DNF Brier (0.1396) was about equal to a constant-rate guess, showing no measurable skill beyond the base rate.</p>
          </Card>
        </div>
        <p className="mt-4 text-xs text-fog">
          Expect the margin over the grid baseline to shrink once it is re-measured with real grid positions. Keep this as the
          before scoreboard.
        </p>
      </Section>

      {summary.length > 0 && (
        <Section eyebrow="Current saved run" title="Latest evaluation report">
          <div className="overflow-x-auto rounded-xl border border-line">
            <table className="w-full min-w-[720px] text-left text-sm">
              <thead className="bg-kerb text-[0.7rem] uppercase tracking-widest text-fog">
                <tr>
                  {["model", "win_log_loss", "podium_brier", "top_3_overlap", "spearman", "position_mae", "dnf_brier_skill"].map((c) => (
                    <th key={c} className="px-4 py-3">{c.replaceAll("_", " ")}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {summary.map((row) => (
                  <tr key={String(row.model)} className="border-t border-line bg-asphalt">
                    {["model", "win_log_loss", "podium_brier", "top_3_overlap", "spearman", "position_mae", "dnf_brier_skill"].map((c) => (
                      <td key={c} className="px-4 py-3 tabular-nums">
                        {typeof row[c] === "number" ? (row[c] as number).toFixed(3) : (row[c] ?? "n/a")}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      )}

      <Section eyebrow="Calibration" title="Are the odds honest?">
        {reliability.length ? (
          <ReliabilityChart rows={reliability} title="Win probability reliability" />
        ) : (
          <Card className="p-6 text-sm text-fog">
            Reliability data is not available for the selected run. Generate it with{" "}
            <code className="rounded bg-track px-1">python main.py evaluate race 2026 &quot;Brazilian Grand Prix&quot;</code>{" "}
            and re-import the saved forecast with the API container&apos;s import-data command. Earlier output showed win and podium bins close to the diagonal at low probabilities; bins above
            50% hold only about ten samples in total, so no conclusion is possible there.
          </Card>
        )}
      </Section>

      {!run && <EmptyState />}
    </>
  );
}
