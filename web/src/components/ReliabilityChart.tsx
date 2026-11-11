import type { ReportRow } from "@/lib/types";

// Reliability diagram: points on the diagonal mean stated probabilities match observed frequencies.
export function ReliabilityChart({ rows, title }: { rows: ReportRow[]; title: string }) {
  const points = rows
    .filter((r) => typeof r.mean_p === "number" && typeof r.hit_rate === "number" && typeof r.n === "number")
    .map((r) => ({ x: r.mean_p as number, y: r.hit_rate as number, n: r.n as number }));
  if (!points.length) return null;
  const size = 320;
  const pad = 36;
  const scale = (v: number) => pad + v * (size - 2 * pad);
  const flip = (v: number) => size - scale(v);
  const maxN = Math.max(...points.map((p) => p.n));
  return (
    <figure>
      <svg viewBox={`0 0 ${size} ${size}`} className="w-full max-w-sm" role="img" aria-label={`${title}: observed frequency against predicted probability`}>
        <rect x={pad} y={pad} width={size - 2 * pad} height={size - 2 * pad} fill="none" stroke="#38384a" />
        {[0.25, 0.5, 0.75].map((t) => (
          <g key={t}>
            <line x1={scale(t)} x2={scale(t)} y1={pad} y2={size - pad} stroke="#2b2b3a" />
            <line y1={flip(t)} y2={flip(t)} x1={pad} x2={size - pad} stroke="#2b2b3a" />
          </g>
        ))}
        <line x1={scale(0)} y1={flip(0)} x2={scale(1)} y2={flip(1)} stroke="#949498" strokeDasharray="4 4" />
        <polyline fill="none" stroke="#e10600" strokeWidth="2" points={points.map((p) => `${scale(p.x)},${flip(p.y)}`).join(" ")} />
        {points.map((p, i) => (
          <circle key={i} cx={scale(p.x)} cy={flip(p.y)} r={3 + 6 * Math.sqrt(p.n / maxN)} fill="#e10600" fillOpacity="0.55" stroke="#fff" strokeWidth="1">
            <title>{`Predicted ${(p.x * 100).toFixed(1)}%, observed ${(p.y * 100).toFixed(1)}% (n=${p.n})`}</title>
          </circle>
        ))}
        <text x={size / 2} y={size - 6} textAnchor="middle" fill="#949498" fontSize="10">Mean predicted probability</text>
        <text transform={`translate(10 ${size / 2}) rotate(-90)`} textAnchor="middle" fill="#949498" fontSize="10">Observed frequency</text>
      </svg>
      <figcaption className="mt-2 text-xs text-fog">Marker size shows the sample count in each bin. Small bins are noisy.</figcaption>
    </figure>
  );
}
