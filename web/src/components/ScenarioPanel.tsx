"use client";

import { useState } from "react";

import { pct, teamColor } from "@/lib/format";
import type { Scenario } from "@/lib/types";

import { Card, Pill, ProbBar } from "./ui";

export function ScenarioPanel({ scenarios }: { scenarios: Record<string, Scenario> }) {
  const keys = Object.keys(scenarios);
  const [active, setActive] = useState(keys[0]);
  if (!keys.length) {
    return <p className="text-sm text-fog">Scenario replay needs saved simulation inputs. Regenerate this forecast with the current model.</p>;
  }
  const scenario = scenarios[active];
  const heuristic = scenario.kind === "heuristic_sensitivity";
  const top = [...scenario.drivers].sort((a, b) => b.simulated_win_prob - a.simulated_win_prob).slice(0, 10);
  const baseline = Object.values(scenarios).find((s) => s.kind === "calibrated_reference");
  const reference = new Map(baseline?.drivers.map((d) => [d.Abbreviation, d.simulated_win_prob]));

  return (
    <div>
      <div className="flex flex-wrap gap-2" role="tablist" aria-label="Scenario">
        {keys.map((key) => (
          <button
            key={key}
            role="tab"
            aria-selected={key === active}
            onClick={() => setActive(key)}
            className={`rounded-full border px-4 py-2 text-xs font-bold uppercase tracking-wider transition-colors ${
              key === active ? "border-red bg-red text-white" : "border-line text-fog hover:border-fog hover:text-paper"
            }`}
          >
            {scenarios[key].name}
          </button>
        ))}
      </div>

      <div className="mt-5 grid gap-5 lg:grid-cols-3">
        <Card className="p-5 lg:col-span-1">
          <Pill tone={heuristic ? "amber" : "teal"}>{heuristic ? "Heuristic sensitivity" : "Calibrated reference"}</Pill>
          <p className="mt-3 text-sm text-fog">{scenario.description}</p>
          <dl className="mt-4 grid grid-cols-2 gap-3 text-xs">
            {[
              ["Safety car", scenario.parameters.safetyCar],
              ["Virtual SC", scenario.parameters.vsc],
              ["Rain", scenario.parameters.rain],
              ["Base DNF", scenario.parameters.dnf],
            ].map(([label, value]) => (
              <div key={label as string}>
                <dt className="eyebrow">{label}</dt>
                <dd className="display mt-1 text-xl">{pct(value as number, 0)}</dd>
              </div>
            ))}
          </dl>
          {heuristic && (
            <p className="mt-4 rounded-md border border-amber/50 bg-amber/10 p-3 text-xs text-amber">
              Weather, safety-car and degradation multipliers are not learned or separately calibrated. Read these as
              what-if sensitivity, not as forecasts.
            </p>
          )}
          <p className="mt-4 text-xs text-fog">
            Scenarios replay saved strengths, retirement risks, temperature, seed and simulation count. They are not an
            independent prediction.
          </p>
        </Card>

        <Card className="p-5 lg:col-span-2">
          <p className="eyebrow mb-4">Win probability · top 10 under this scenario</p>
          <ul className="space-y-3">
            {top.map((d) => {
              const delta = d.simulated_win_prob - (reference.get(d.Abbreviation) ?? d.simulated_win_prob);
              return (
                <li key={d.Abbreviation} className="grid grid-cols-[3.5rem_1fr_4.5rem_4.5rem] items-center gap-3 text-sm">
                  <span className="font-bold">{d.Abbreviation}</span>
                  <ProbBar value={d.simulated_win_prob} max={0.5} color={teamColor(d.TeamName)} label={`${d.Abbreviation} ${pct(d.simulated_win_prob)}`} />
                  <span className="text-right font-bold tabular-nums">{pct(d.simulated_win_prob)}</span>
                  <span className={`text-right text-xs tabular-nums ${delta > 0.0005 ? "text-teal" : delta < -0.0005 ? "text-red" : "text-fog"}`}>
                    {scenario.kind === "calibrated_reference" ? "reference" : `${delta >= 0 ? "+" : ""}${(delta * 100).toFixed(1)} pts`}
                  </span>
                </li>
              );
            })}
          </ul>
        </Card>
      </div>
    </div>
  );
}
