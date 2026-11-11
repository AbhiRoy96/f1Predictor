import { EmptyState, PageHeader, loadRun, type PageProps } from "@/components/PageHeader";
import { Card, TeamTag } from "@/components/ui";
import { num, pct, teamColor } from "@/lib/format";

export default async function DriversPage(props: PageProps) {
  const { run, manifest } = await loadRun(props);
  if (!run) return <EmptyState />;
  const drivers = [...run.drivers].sort((a, b) => a.expected_position - b.expected_position);

  return (
    <>
      <PageHeader
        eyebrow="Drivers"
        title="Driver outlook"
        subtitle="Each bar chart is that driver's simulated finishing distribution, from P1 on the left to the back of the field on the right."
        run={run}
        manifest={manifest}
      />
      <div className="mt-8 grid gap-5 sm:grid-cols-2 xl:grid-cols-3">
        {drivers.map((d) => {
          const color = teamColor(d.TeamName);
          const peak = Math.max(...d.positionProbabilities, 0.001);
          return (
            <Card key={d.Abbreviation} accent={color} className="p-5">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p className="display text-2xl leading-tight">{d.DriverName ?? d.Abbreviation}</p>
                  <TeamTag team={d.TeamName} />
                </div>
                <span className="rounded px-2 py-1 text-sm font-black text-track" style={{ background: color }}>{d.Abbreviation}</span>
              </div>
              <div className="mt-5 grid grid-cols-4 gap-2 text-center">
                {[
                  ["Win", pct(d.win_probability)],
                  ["Podium", pct(d.podium_probability, 0)],
                  ["Top 10", pct(d.top10_probability, 0)],
                  ["DNF", pct(d.dnf_probability, 0)],
                ].map(([label, value]) => (
                  <div key={label} className="rounded-md bg-kerb py-2">
                    <p className="eyebrow !text-[0.58rem]">{label}</p>
                    <p className="mt-1 text-sm font-bold tabular-nums">{value}</p>
                  </div>
                ))}
              </div>
              <div
                className="mt-5 flex h-20 items-end gap-[2px]"
                role="img"
                aria-label={`${d.Abbreviation} finishing distribution, most likely around P${d.median_position ?? Math.round(d.expected_position)}`}
              >
                {d.positionProbabilities.map((p, i) => (
                  <div key={i} className="flex-1 rounded-t-[2px]" style={{ height: `${Math.max(3, (p / peak) * 100)}%`, background: color, opacity: 0.35 + 0.65 * (p / peak) }} title={`P${i + 1}: ${pct(p)}`} />
                ))}
              </div>
              <div className="mt-1 flex justify-between text-[0.6rem] font-bold text-fog"><span>P1</span><span>P{d.positionProbabilities.length}</span></div>
              <p className="mt-4 text-xs text-fog">
                Expected P{num(d.expected_position, 1)}
                {d.median_position != null && ` · median P${d.median_position}`}
                {d.p10_finish != null && d.p90_finish != null && ` · P${d.p10_finish}–P${d.p90_finish} if he finishes`}
              </p>
            </Card>
          );
        })}
      </div>
      <p className="mt-6 text-xs text-fog">
        Career statistics and portraits are intentionally not shown: the previous prototype hard-coded them, and this
        interface only displays values that come from saved model output.
      </p>
    </>
  );
}
