import { pct, teamColor } from "@/lib/format";
import type { DriverForecast } from "@/lib/types";

// Rows are drivers, columns are finishing positions; opacity encodes probability.
export function Heatmap({ drivers }: { drivers: DriverForecast[] }) {
  const size = drivers[0]?.positionProbabilities.length ?? 0;
  if (!size) return <p className="text-sm text-fog">Finishing-position probabilities are not available for this run.</p>;
  const peak = Math.max(...drivers.flatMap((d) => d.positionProbabilities));
  return (
    <div className="overflow-x-auto rounded-xl border border-line bg-asphalt p-4">
      <div className="min-w-[760px]" role="table" aria-label="Probability of each finishing position by driver">
        <div className="mb-1 grid items-center gap-[2px]" style={{ gridTemplateColumns: `72px repeat(${size}, minmax(0, 1fr))` }} role="row">
          <span />
          {Array.from({ length: size }, (_, i) => (
            <span key={i} className="text-center text-[0.6rem] font-bold text-fog" role="columnheader">P{i + 1}</span>
          ))}
        </div>
        {drivers.map((d) => {
          const color = teamColor(d.TeamName);
          return (
            <div key={d.Abbreviation} className="mb-[2px] grid items-center gap-[2px]" style={{ gridTemplateColumns: `72px repeat(${size}, minmax(0, 1fr))` }} role="row">
              <span className="flex items-center gap-2 text-xs font-bold" role="rowheader">
                <span className="h-4 w-1 rounded-sm" style={{ background: color }} />
                {d.Abbreviation}
              </span>
              {d.positionProbabilities.map((p, i) => (
                <span
                  key={i}
                  role="cell"
                  title={`${d.Abbreviation} finishes P${i + 1}: ${pct(p)}`}
                  className="h-6 rounded-[3px]"
                  style={{ background: color, opacity: p <= 0 ? 0.04 : 0.1 + 0.9 * (p / peak) }}
                />
              ))}
            </div>
          );
        })}
      </div>
      <p className="mt-3 text-xs text-fog">
        Each row sums to 100% across positions (retirements are classified behind finishers). Darker cells are more likely.
      </p>
    </div>
  );
}
