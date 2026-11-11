import Link from "next/link";
import type { ReactNode } from "react";

import { DataNotice } from "@/components/DataNotice";
import { EmptyState, loadRun, type PageProps } from "@/components/PageHeader";
import { RunPicker } from "@/components/RunPicker";
import { Card, Pill, ProbBar, Section, TeamTag } from "@/components/ui";
import { num, pct, sessionLabel, stageLabel, teamColor } from "@/lib/format";

const icon = (d: ReactNode) => (
  <svg viewBox="0 0 24 24" className="h-7 w-7" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{d}</svg>
);

const FEATURES = [
  { href: "/predictions", title: "Every outcome, quantified", text: "Win, podium, top-10 and retirement odds for the whole grid, from tens of thousands of simulated races.",
    icon: icon(<path d="M4 20V10M10 20V4M16 20v-7M22 20H2" />) },
  { href: "/predictions", title: "Finishing-position heatmap", text: "See the full P1 to P22 probability spread for every driver, not just the favourites.",
    icon: icon(<><rect x="3" y="3" width="7" height="7" /><rect x="14" y="3" width="7" height="7" /><rect x="3" y="14" width="7" height="7" /><rect x="14" y="14" width="7" height="7" /></>) },
  { href: "/predictions", title: "What-if scenarios", text: "Wet chaos, safety-car mayhem, tyre cliffs, attrition. Replay the same race under different conditions.",
    icon: icon(<><path d="M7 18a4 4 0 0 1-.5-7.97A6 6 0 0 1 18 8.5a3.5 3.5 0 0 1-.5 7" /><path d="M8 20l1-3M12 20l1-3M16 20l1-3" /></>) },
  { href: "/drivers", title: "Driver outlook cards", text: "A finishing distribution, retirement risk and expected position for every driver on the grid.",
    icon: icon(<><circle cx="12" cy="8" r="4" /><path d="M4 21a8 8 0 0 1 16 0" /></>) },
  { href: "/teams", title: "Team battle", text: "Combined win chances, expected podiums and points scorers for each constructor.",
    icon: icon(<><path d="M8 21h8M12 17v4M7 4h10v5a5 5 0 0 1-10 0z" /><path d="M7 6H4a3 3 0 0 0 3 4M17 6h3a3 3 0 0 1-3 4" /></>) },
  { href: "/lab", title: "Proof, not promises", text: "Walk-forward backtests against grid, championship and team baselines, with calibration and caveats.",
    icon: icon(<path d="M9 3h6M10 3v6L4.5 19a2 2 0 0 0 1.8 3h11.4a2 2 0 0 0 1.8-3L14 9V3" />) },
];

const PILLARS = [
  ["No peeking at the future", "Every feature comes from sessions that finished before the cutoff. The race result is never an input."],
  ["Odds that add up", "Win sums to 100%, podium to 300%, top-10 to 1000%. Every table is checked for consistency before it is shown."],
  ["Beat the baseline or it does not count", "The model is scored against simple benchmarks on races it has never seen, using block-bootstrap intervals."],
  ["Honest about uncertainty", "Ranges describe simulated outcomes, what-if scenarios are labelled heuristic, and thin evidence is flagged."],
];

const STAGES = [
  ["Pre-weekend", "History only"],
  ["After practice", "Plus practice pace"],
  ["After qualifying", "Plus the grid"],
];

export default async function HomePage(props: PageProps) {
  const { run, manifest } = await loadRun(props);
  if (!run) return <EmptyState />;
  const { meta, drivers } = run;
  const ranked = [...drivers].sort((a, b) => b.win_probability - a.win_probability);
  const leader = ranked[0];
  const event = meta.event_name ?? "Upcoming event";
  const suffix = `?run=${encodeURIComponent(meta.id)}`;
  const scenarioCount = Object.keys(run.scenarios).length;

  return (
    <>
      <section className="chequer relative overflow-hidden rounded-2xl border border-line">
        <div className="stripe absolute inset-0" />
        <div className="absolute inset-0 bg-gradient-to-r from-track via-track/90 to-red/10" />
        <div className="relative px-6 py-14 sm:px-12 sm:py-20">
          <div className="flex flex-wrap gap-2">
            <Pill tone="red">Race Intelligence</Pill>
            <Pill>Point-in-time</Pill>
            <Pill>Monte Carlo</Pill>
            <Pill>Unofficial</Pill>
          </div>
          <h1 className="display mt-8 text-6xl leading-[0.9] sm:text-8xl lg:text-9xl">
            Grid<span className="text-red">cast</span>
          </h1>
          <p className="display mt-4 max-w-3xl text-2xl leading-tight sm:text-4xl">
            Know the odds before the lights go out.
          </p>
          <p className="mt-6 max-w-2xl text-base text-fog sm:text-lg">
            Gridcast turns qualifying, practice pace, form and reliability into calibrated win, podium and retirement
            probabilities for every driver, then simulates the race tens of thousands of times so you can see the whole
            picture, not just the favourite.
          </p>
          <div className="mt-9 flex flex-wrap gap-3">
            <Link href={`/predictions${suffix}`} className="rounded-full bg-red px-8 py-4 text-sm font-bold uppercase tracking-wider transition-colors hover:bg-red-deep">
              See the forecast
            </Link>
            <Link href="/method" className="rounded-full border border-line px-8 py-4 text-sm font-bold uppercase tracking-wider transition-colors hover:border-paper">
              How it works
            </Link>
          </div>
          <dl className="mt-12 grid max-w-3xl grid-cols-2 gap-6 border-t border-line pt-8 sm:grid-cols-4">
            {[
              [meta.simulation_count?.toLocaleString() ?? "n/a", "Simulated races"],
              [drivers.length, "Drivers modelled"],
              [scenarioCount || "n/a", "What-if scenarios"],
              ["3", "Forecast stages"],
            ].map(([value, label]) => (
              <div key={label as string}>
                <dd className="display text-4xl">{value}</dd>
                <dt className="eyebrow mt-1">{label}</dt>
              </div>
            ))}
          </dl>
        </div>
      </section>

      <div className="mt-6 flex flex-wrap items-center justify-between gap-4">
        <div className="min-w-0 flex-1"><DataNotice meta={meta} /></div>
        <RunPicker manifest={manifest} current={meta.id} />
      </div>

      <Section eyebrow="Up next" title={event}>
        <div className="grid gap-5 lg:grid-cols-[1.2fr_1fr]">
          <Card className="p-6" accent={teamColor(leader.TeamName)}>
            <div className="flex flex-wrap gap-2">
              <Pill tone="red">{meta.demo ? "Illustrative sample" : "Latest forecast"}</Pill>
              <Pill>{stageLabel(meta.stage)}</Pill>
              <Pill>{sessionLabel(meta.session)}</Pill>
            </div>
            <p className="eyebrow mt-6">Model favourite</p>
            <p className="display mt-1 text-5xl">{leader.DriverName ?? leader.Abbreviation}</p>
            <TeamTag team={leader.TeamName} />
            <p className="display mt-5 text-7xl text-red">{pct(leader.win_probability)}</p>
            <p className="text-sm text-fog">
              to win · {pct(1 - leader.win_probability, 0)} that someone else does · expected finish P{num(leader.expected_position, 1)}
            </p>
          </Card>
          <Card className="p-6">
            <p className="eyebrow mb-4">Top five to win</p>
            <ul className="space-y-4">
              {ranked.slice(0, 5).map((d, i) => (
                <li key={d.Abbreviation} className="grid grid-cols-[1.5rem_3rem_1fr_4rem] items-center gap-3 text-sm">
                  <span className="display text-fog">{i + 1}</span>
                  <span className="font-bold">{d.Abbreviation}</span>
                  <ProbBar value={d.win_probability} max={Math.max(leader.win_probability, 0.01)} color={teamColor(d.TeamName)} label={`${d.Abbreviation} ${pct(d.win_probability)}`} />
                  <span className="text-right font-bold tabular-nums">{pct(d.win_probability)}</span>
                </li>
              ))}
            </ul>
            <Link href={`/predictions${suffix}`} className="mt-6 inline-block text-xs font-bold uppercase tracking-widest text-red">Full grid →</Link>
          </Card>
        </div>
      </Section>

      <Section eyebrow="Everything in one paddock" title="Built to see the whole race">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {FEATURES.map((f) => (
            <Link key={f.title} href={`${f.href}${suffix}`} className="group">
              <Card className="h-full p-6 transition-colors group-hover:border-red">
                <span className="grid h-12 w-12 place-items-center rounded-lg bg-red/15 text-red">{f.icon}</span>
                <h3 className="display mt-4 text-xl group-hover:text-red">{f.title}</h3>
                <p className="mt-2 text-sm text-fog">{f.text}</p>
              </Card>
            </Link>
          ))}
        </div>
      </Section>

      <Section eyebrow="Forecast at every stage" title="Smarter as the weekend unfolds">
        <ol className="grid gap-4 md:grid-cols-3">
          {STAGES.map(([title, text], i) => (
            <li key={title}>
              <Card className="relative h-full p-6">
                <span className="display absolute right-5 top-3 text-7xl text-kerb">{i + 1}</span>
                <h3 className="display relative text-2xl">{title}</h3>
                <p className="relative mt-2 text-sm text-fog">{text}. Each stage only uses what was known when it began.</p>
              </Card>
            </li>
          ))}
        </ol>
      </Section>

      <Section eyebrow="Why trust it" title="Honest by design">
        <div className="grid gap-4 md:grid-cols-2">
          {PILLARS.map(([title, text]) => (
            <Card key={title} className="flex gap-4 p-6">
              <span className="mt-1 h-10 w-1 flex-none rounded bg-red" />
              <div>
                <h3 className="font-bold">{title}</h3>
                <p className="mt-1 text-sm text-fog">{text}</p>
              </div>
            </Card>
          ))}
        </div>
      </Section>

      <section className="stripe mt-16 overflow-hidden rounded-2xl border border-red/40 bg-asphalt p-8 text-center sm:p-14">
        <h2 className="display text-4xl sm:text-5xl">Lights out in <span className="text-red">3, 2, 1</span></h2>
        <p className="mx-auto mt-4 max-w-xl text-fog">Dive into the full forecast, stress-test it with scenarios, and check the receipts in the Model Lab.</p>
        <div className="mt-7 flex flex-wrap justify-center gap-3">
          <Link href={`/predictions${suffix}`} className="rounded-full bg-red px-8 py-4 text-sm font-bold uppercase tracking-wider hover:bg-red-deep">Open predictions</Link>
          <Link href="/lab" className="rounded-full border border-line px-8 py-4 text-sm font-bold uppercase tracking-wider hover:border-paper">Visit the Model Lab</Link>
        </div>
      </section>
    </>
  );
}
