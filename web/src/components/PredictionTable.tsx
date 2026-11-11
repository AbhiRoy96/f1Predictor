"use client";

import { useMemo, useState } from "react";

import { num, pct, teamColor } from "@/lib/format";
import type { DriverForecast } from "@/lib/types";

import { ProbBar } from "./ui";

type Key = "win_probability" | "podium_probability" | "top10_probability" | "dnf_probability" | "expected_position";

const COLUMNS: { key: Key; label: string; asc?: boolean }[] = [
  { key: "win_probability", label: "Win" },
  { key: "podium_probability", label: "Podium" },
  { key: "top10_probability", label: "Top 10" },
  { key: "dnf_probability", label: "DNF", asc: true },
  { key: "expected_position", label: "Exp. pos", asc: true },
];

export function PredictionTable({ drivers }: { drivers: DriverForecast[] }) {
  const [sort, setSort] = useState<Key>("win_probability");
  const [ascending, setAscending] = useState(false);
  const rows = useMemo(() => {
    const sorted = [...drivers].sort((a, b) => a[sort] - b[sort]);
    return ascending ? sorted : sorted.reverse();
  }, [drivers, sort, ascending]);
  const hasFinishRange = drivers.some((d) => d.p10_finish != null);

  function choose(key: Key, defaultAscending = false) {
    if (key === sort) setAscending(!ascending);
    else {
      setSort(key);
      setAscending(defaultAscending);
    }
  }

  return (
    <div className="overflow-x-auto rounded-xl border border-line">
      <table className="w-full min-w-[860px] text-left text-sm">
        <caption className="sr-only">Driver forecast probabilities, sortable by column</caption>
        <thead className="bg-kerb text-[0.7rem] uppercase tracking-widest text-fog">
          <tr>
            <th scope="col" className="px-4 py-3">Pos</th>
            <th scope="col" className="px-4 py-3">Driver</th>
            {COLUMNS.map(({ key, label, asc }) => (
              <th key={key} scope="col" aria-sort={sort === key ? (ascending ? "ascending" : "descending") : "none"} className="px-4 py-3">
                <button type="button" onClick={() => choose(key, asc)} className="font-bold uppercase tracking-widest hover:text-paper">
                  {label}{sort === key ? (ascending ? " ▲" : " ▼") : ""}
                </button>
              </th>
            ))}
            <th scope="col" className="px-4 py-3">{hasFinishRange ? "Finish range (if finishes)" : "P10–P90"}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((d, index) => {
            const color = teamColor(d.TeamName);
            const low = hasFinishRange ? d.p10_finish : d.p10_position;
            const high = hasFinishRange ? d.p90_finish : d.p90_position;
            return (
              <tr key={d.Abbreviation} className="border-t border-line bg-asphalt transition-colors hover:bg-kerb">
                <td className="px-4 py-3 font-bold tabular-nums text-fog">{index + 1}</td>
                <td className="px-4 py-3">
                  <div className="flex items-center gap-3">
                    <span className="h-9 w-1 rounded-sm" style={{ background: color }} />
                    <div>
                      <p className="font-bold">{d.DriverName ?? d.Abbreviation}</p>
                      <p className="text-xs text-fog">{d.Abbreviation} · {d.TeamName ?? "Unknown team"}</p>
                    </div>
                  </div>
                </td>
                <td className="px-4 py-3">
                  <p className="font-bold tabular-nums">{pct(d.win_probability)}</p>
                  <div className="mt-1 w-24"><ProbBar value={d.win_probability} max={0.6} color={color} label={`Win probability ${pct(d.win_probability)}`} /></div>
                </td>
                <td className="px-4 py-3 tabular-nums">{pct(d.podium_probability)}</td>
                <td className="px-4 py-3 tabular-nums">{pct(d.top10_probability)}</td>
                <td className="px-4 py-3 tabular-nums text-fog">{pct(d.dnf_probability)}</td>
                <td className="px-4 py-3 tabular-nums">P{num(d.expected_position, 1)}</td>
                <td className="px-4 py-3 tabular-nums text-fog">
                  {low != null && high != null ? `P${low} – P${high}` : "n/a"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
