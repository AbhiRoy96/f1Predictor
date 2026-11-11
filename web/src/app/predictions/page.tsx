import { Heatmap } from "@/components/Heatmap";
import { EmptyState, PageHeader, loadRun, type PageProps } from "@/components/PageHeader";
import { PredictionTable } from "@/components/PredictionTable";
import { ScenarioPanel } from "@/components/ScenarioPanel";
import { Section, Stat } from "@/components/ui";
import { num, pct, sessionLabel, stageLabel } from "@/lib/format";

export default async function PredictionsPage(props: PageProps) {
  const { run, manifest } = await loadRun(props);
  if (!run) return <EmptyState />;
  const { meta, drivers } = run;
  const leader = [...drivers].sort((a, b) => b.win_probability - a.win_probability)[0];
  const sumWin = drivers.reduce((total, d) => total + d.win_probability, 0);
  const sumPodium = drivers.reduce((total, d) => total + d.podium_probability, 0);
  const generated = meta.prediction_generated_at_utc ? new Date(meta.prediction_generated_at_utc).toUTCString() : "unknown";

  return (
    <>
      <PageHeader
        eyebrow={`${meta.year ?? ""} · ${stageLabel(meta.stage)} · ${sessionLabel(meta.session)}`}
        title={`${meta.event_name ?? "Race"} predictions`}
        subtitle="Every figure is a probability from simulated races using only evidence available before the stage cutoff."
        run={run}
        manifest={manifest}
      />

      <div className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Favourite" value={leader.Abbreviation} hint={leader.DriverName ?? undefined} />
        <Stat label="Win probability" value={pct(leader.win_probability)} tone="teal" hint={`± ${pct(leader.win_probability_mcse ?? null, 2)} sampling error`} />
        <Stat label="Podium probability" value={pct(leader.podium_probability)} />
        <Stat label="Expected finish" value={`P${num(leader.expected_position, 1)}`} />
      </div>

      <Section eyebrow="Probabilities" title="Driver forecast">
        <PredictionTable drivers={drivers} />
        <p className="mt-3 text-xs text-fog">
          Consistency check: win probabilities sum to {pct(sumWin, 1)} and podium probabilities to {num(sumPodium, 2)} (expected
          1.00 and 3.00). The finish range is conditional on the driver not retiring and describes simulated outcomes, not a
          parameter confidence interval.
        </p>
      </Section>

      <Section eyebrow="Distribution" title="Finishing-position heatmap">
        <Heatmap drivers={drivers} />
      </Section>

      <Section eyebrow="What if" title="Scenario analysis">
        <ScenarioPanel scenarios={run.scenarios} />
      </Section>

      <Section eyebrow="Provenance" title="About this forecast">
        <dl className="grid gap-4 rounded-xl border border-line bg-asphalt p-6 text-sm sm:grid-cols-2 lg:grid-cols-4">
          {[
            ["Generated (UTC)", generated],
            ["Model", `${meta.strength_model ?? "n/a"} · ${meta.model_version ?? "n/a"}`],
            ["Training window", meta.training_start_utc ? `${meta.training_start_utc.slice(0, 10)} to ${meta.training_end_utc?.slice(0, 10) ?? "?"}` : "n/a"],
            ["Training races", meta.training_races ?? "n/a"],
            ["Simulations", meta.simulation_count?.toLocaleString() ?? "n/a"],
            ["Seed", meta.seed ?? "n/a"],
            ["PL temperature", meta.pl_temperature != null ? num(meta.pl_temperature, 3) : "n/a"],
            ["Grid source", drivers.some((d) => d.grid_is_quali_proxy === 1) ? "Qualifying order proxy" : "Official or eligible grid"],
          ].map(([label, value]) => (
            <div key={label as string}>
              <dt className="eyebrow">{label}</dt>
              <dd className="mt-1 break-words font-bold">{value}</dd>
            </div>
          ))}
        </dl>
      </Section>
    </>
  );
}
