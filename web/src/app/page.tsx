import Link from "next/link";

import { Card, Pill, Section } from "@/components/ui";

const AREAS = [
  ["/predictions", "Predictions", "Create forecasts, follow background jobs, compare driver and team outlooks, and explore finishing distributions and what-if scenarios."],
  ["/seasons", "Seasons & history", "Browse season calendars, race and sprint classifications, driver standings and constructor standings from PostgreSQL."],
  ["/lab", "Model Lab", "Inspect saved walk-forward reports, calibration and baseline comparisons. Evidence matters more than a confident headline."],
  ["/settings", "Settings", "Choose prediction defaults for model, stage, simulation count, seed and data collection. No provider API key is currently required."],
];

export default function HomePage() {
  return (
    <>
      <section className="chequer relative overflow-hidden rounded-2xl border border-line">
        <div className="stripe absolute inset-0" />
        <div className="relative bg-track/90 px-6 py-16 sm:px-12">
          <div className="flex flex-wrap gap-2"><Pill tone="red">Race Intelligence</Pill><Pill>Unofficial fan project</Pill></div>
          <h1 className="display mt-8 text-6xl sm:text-8xl">Grid<span className="text-red">cast</span></h1>
          <p className="display mt-6 text-3xl">Your Formula 1 intelligence workspace.</p>
          <p className="mt-6 max-w-3xl text-lg text-fog">
            Explore seasons past and present, build probabilistic forecasts, and examine the evidence behind the models.
            Gridcast brings race history, simulation and evaluation into one place.
          </p>
        </div>
      </section>
      <Section eyebrow="Explore the app" title="One workspace, clear destinations">
        <div className="grid gap-5 md:grid-cols-2">
          {AREAS.map(([href, title, text]) => (
            <Link key={href} href={href} className="rounded-xl focus-visible:outline-red">
              <Card className="h-full p-7">
                <h2 className="display text-2xl">{title}</h2><p className="mt-3 text-fog">{text}</p>
                <span className="mt-5 inline-block text-sm font-bold text-red">Explore {title}</span>
              </Card>
            </Link>
          ))}
        </div>
      </Section>
      <Section eyebrow="Principles" title="Evidence before certainty">
        <div className="grid gap-5 md:grid-cols-3">
          {[
            ["Point-in-time evidence", "Forecast inputs respect stage cutoffs. Target outcomes are reserved for evaluation, not fed back into their own prediction."],
            ["Transparent uncertainty", "Probabilities describe simulated outcomes, not guarantees. Heuristic scenarios and incomplete historical coverage are labelled."],
            ["Persistent by design", "Flask serves PostgreSQL-backed forecasts and season records. A separate worker handles long-running prediction and sync jobs."],
          ].map(([title, text]) => <Card key={title} className="p-6"><h3 className="font-bold">{title}</h3><p className="mt-3 text-sm text-fog">{text}</p></Card>)}
        </div>
      </Section>
    </>
  );
}
