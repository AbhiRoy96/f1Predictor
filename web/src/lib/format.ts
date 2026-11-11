const TEAM_COLORS: Record<string, string> = {
  McLaren: "#FF8000",
  Mercedes: "#27F4D2",
  "Red Bull Racing": "#3671C6",
  Ferrari: "#E80020",
  Williams: "#64C4FF",
  "Racing Bulls": "#6692FF",
  "Aston Martin": "#229971",
  Haas: "#B6BABD",
  Audi: "#F50537",
  Alpine: "#0093CC",
  Cadillac: "#AAAAAA",
  "Kick Sauber": "#52E252",
};

export function teamColor(team?: string | null): string {
  return (team && TEAM_COLORS[team]) || "#E10600";
}

export const pct = (value: number | null | undefined, digits = 1): string =>
  value == null || Number.isNaN(value) ? "n/a" : `${(value * 100).toFixed(digits)}%`;

export const num = (value: number | null | undefined, digits = 2): string =>
  value == null || Number.isNaN(value) ? "n/a" : value.toFixed(digits);

export const stageLabel = (stage?: string): string =>
  ({ pre_weekend: "Pre-weekend", post_fp: "After practice", post_quali: "After qualifying" })[stage ?? ""] ?? "Unknown stage";

export const sessionLabel = (session?: string): string =>
  ({ race: "Grand Prix", sprint: "Sprint", qualifying: "Qualifying" })[session ?? ""] ?? "Session";
