import { EmptyState, PageHeader, loadRun, type PageProps } from "@/components/PageHeader";
import { Card, ProbBar } from "@/components/ui";
import { num, pct, teamColor } from "@/lib/format";

export default async function TeamsPage(props: PageProps) {
  const { run, manifest } = await loadRun(props);
  if (!run) return <EmptyState />;

  const grouped = new Map<string, typeof run.drivers>();
  for (const d of run.drivers) {
    const key = d.TeamName ?? "Unknown";
    grouped.set(key, [...(grouped.get(key) ?? []), d]);
  }
  const teams = [...grouped.entries()]
    .map(([name, members]) => ({
      name,
      members,
      win: members.reduce((t, d) => t + d.win_probability, 0),
      podiums: members.reduce((t, d) => t + d.podium_probability, 0),
      points: members.reduce((t, d) => t + d.top10_probability, 0),
      dnf: members.reduce((t, d) => t + d.dnf_probability, 0) / members.length,
    }))
    .sort((a, b) => b.win - a.win);
  const peak = Math.max(...teams.map((t) => t.win), 0.01);

  return (
    <>
      <PageHeader
        eyebrow="Constructors"
        title="Team outlook"
        subtitle="Team figures add the two drivers' marginal forecasts. They show where the weekend's chances sit, not a separate team model."
        run={run}
        manifest={manifest}
      />
      <div className="mt-8 grid gap-5 lg:grid-cols-2">
        {teams.map((team, index) => {
          const color = teamColor(team.name);
          return (
            <Card key={team.name} accent={color} className="p-6">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-4">
                  <span className="display text-4xl text-fog">{index + 1}</span>
                  <p className="display text-2xl">{team.name}</p>
                </div>
                <span className="display text-3xl" style={{ color }}>{pct(team.win)}</span>
              </div>
              <div className="mt-3"><ProbBar value={team.win} max={peak} color={color} label={`${team.name} combined win probability ${pct(team.win)}`} /></div>
              <dl className="mt-5 grid grid-cols-3 gap-3 text-sm">
                <div><dt className="eyebrow">Exp. podiums</dt><dd className="mt-1 font-bold tabular-nums">{num(team.podiums, 2)} of 2</dd></div>
                <div><dt className="eyebrow">Exp. points scorers</dt><dd className="mt-1 font-bold tabular-nums">{num(team.points, 2)} of 2</dd></div>
                <div><dt className="eyebrow">Avg. DNF risk</dt><dd className="mt-1 font-bold tabular-nums">{pct(team.dnf)}</dd></div>
              </dl>
              <ul className="mt-5 divide-y divide-line border-t border-line text-sm">
                {team.members.map((d) => (
                  <li key={d.Abbreviation} className="flex items-center justify-between py-2">
                    <span><strong>{d.Abbreviation}</strong> <span className="text-fog">{d.DriverName}</span></span>
                    <span className="tabular-nums text-fog">P{num(d.expected_position, 1)} · win {pct(d.win_probability)}</span>
                  </li>
                ))}
              </ul>
            </Card>
          );
        })}
      </div>
      <p className="mt-6 text-xs text-fog">
        Retirement risk is correlated within a race in the simulator, so adding marginals describes expectations only.
        Constructor standings are not shown because the collected history does not hold complete championship data.
      </p>
    </>
  );
}
