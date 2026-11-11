import { PageHeader, type PageProps } from "@/components/PageHeader";
import { Card, Section } from "@/components/ui";

const STEPS = [
  { n: "01", title: "Collect", text: "FastF1 sessions (practice, qualifying, sprint, race) are saved with scheduled UTC start times." },
  { n: "02", title: "Point-in-time features", text: "Every feature is computed from sessions that began strictly before the stage cutoff. Targets never become inputs." },
  { n: "03", title: "Strength models", text: "A Bayesian driver and constructor model and an XGBoost ranker are blended on out-of-fold scores." },
  { n: "04", title: "Retirement model", text: "Mechanical, collision and other causes are shrunk by entity and recency, with a shared race-level shock." },
  { n: "05", title: "Simulate", text: "Plackett-Luce finishing orders with retirements give win, podium, top-10 and full position probabilities." },
  { n: "06", title: "Evaluate", text: "Chronological walk-forward backtests compare against grid, qualifying, championship and team baselines." },
];

const STAGES = [
  ["Pre-weekend", "No same-weekend evidence. The cutoff is the earliest weekend session start."],
  ["After practice", "Earlier practice observations only, before the first non-practice session."],
  ["After qualifying", "Earlier sessions including qualifying. The target session result is never used."],
];

export default async function MethodPage(props: PageProps) {
  await props.searchParams;
  return (
    <>
      <PageHeader
        eyebrow="Method"
        title="How the forecast thinks"
        subtitle="The same pipeline the Streamlit prototype used, explained step by step: evidence first, simulation second, honesty throughout."
      />

      <Section eyebrow="Pipeline" title="From sessions to probabilities">
        <ol className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {STEPS.map((step) => (
            <li key={step.n}>
              <Card className="h-full p-6">
                <p className="display text-4xl text-red">{step.n}</p>
                <h3 className="display mt-2 text-xl">{step.title}</h3>
                <p className="mt-2 text-sm text-fog">{step.text}</p>
              </Card>
            </li>
          ))}
        </ol>
      </Section>

      <Section eyebrow="Leakage control" title="Prediction stages">
        <div className="grid gap-4 md:grid-cols-3">
          {STAGES.map(([title, text]) => (
            <Card key={title} className="p-6" accent="#e10600">
              <h3 className="display text-xl">{title}</h3>
              <p className="mt-2 text-sm text-fog">{text}</p>
            </Card>
          ))}
        </div>
        <p className="mt-4 text-sm text-fog">
          For finished events the forecast is reconstructed from a pre-session cutoff and the real result is reserved for
          evaluation. Official grids count only when their publication time precedes the cutoff. Otherwise qualifying order
          stands in as a flagged proxy, with no assumed penalties.
        </p>
      </Section>

      <Section eyebrow="Reading the numbers" title="What each figure means">
        <div className="grid gap-4 md:grid-cols-2">
          {[
            ["Win, podium, top-10", "Chance of finishing P1, within P3 and within P10 across simulated races. Win sums to 100%, podium to 300% and top-10 to 1000% across the field."],
            ["DNF", "Chance of retiring, from the retirement model. Retirees are classified behind finishers in the simulator."],
            ["Finish range", "P10 to P90 of the simulated finishing position, conditional on not retiring. It is outcome spread, not model uncertainty."],
            ["Sampling error", "Monte Carlo standard error of the win probability. It reflects the number of simulations only."],
            ["Scenarios", "Standard dry replays the calibrated reference. Other scenarios apply fixed heuristic multipliers and are what-if sensitivity checks."],
            ["Model Lab", "Walk-forward metrics on held-out races. Intervals use a block bootstrap and are withheld with fewer than five races."],
          ].map(([title, text]) => (
            <Card key={title} className="p-5">
              <h3 className="font-bold">{title}</h3>
              <p className="mt-1 text-sm text-fog">{text}</p>
            </Card>
          ))}
        </div>
      </Section>

      <Section eyebrow="Limits" title="Known simplifications">
        <ul className="list-disc space-y-2 pl-5 text-sm text-fog">
          <li>Plackett-Luce relative odds, a constant-hazard retirement-distance proxy and finishers ahead of retirees.</li>
          <li>No lap-by-lap fuel, pit-strategy or lapped-car classification model.</li>
          <li>Training uses collected events, not complete championship history; sampled circuits over-represent some tracks.</li>
          <li>Probabilities are conditional outcomes, not confidence intervals on model parameters.</li>
        </ul>
      </Section>
    </>
  );
}
