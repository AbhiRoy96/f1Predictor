export interface DriverForecast {
  Abbreviation: string;
  DriverName?: string | null;
  TeamName?: string | null;
  win_probability: number;
  podium_probability: number;
  top10_probability: number;
  dnf_probability: number;
  expected_position: number;
  median_position?: number | null;
  p10_position?: number | null;
  p90_position?: number | null;
  p10_finish?: number | null;
  p90_finish?: number | null;
  vs_grid_order?: number | null;
  win_probability_mcse?: number | null;
  grid_position?: number | null;
  grid_is_quali_proxy?: number | null;
  positionProbabilities: number[];
}

export interface ScenarioDriver {
  Abbreviation: string;
  TeamName?: string | null;
  simulated_win_prob: number;
  simulated_podium_prob: number;
  simulated_dnf_prob: number;
  expected_position: number;
  top10_probability?: number | null;
}

export interface Scenario {
  name: string;
  description: string;
  kind: "calibrated_reference" | "heuristic_sensitivity";
  parameters: { safetyCar: number; vsc: number; rain: number; dnf: number; overtakingDifficulty: number };
  drivers: ScenarioDriver[];
}

export interface RunMeta {
  id: string;
  demo: boolean;
  session?: string;
  year?: number;
  event_name?: string;
  round_number?: number;
  stage?: string;
  model_version?: string;
  strength_model?: string;
  prediction_generated_at_utc?: string;
  training_start_utc?: string;
  training_end_utc?: string;
  training_races?: number;
  simulation_count?: number;
  seed?: number;
  pl_temperature?: number;
  history_scope?: string;
}

export type ReportRow = Record<string, string | number | null>;

export interface Run {
  meta: RunMeta;
  drivers: DriverForecast[];
  scenarios: Record<string, Scenario>;
  reports: { summary?: ReportRow[]; confidence?: ReportRow[]; reliability?: ReportRow[] };
}

export interface ManifestEntry {
  id: string;
  event_name?: string | null;
  year?: number | null;
  session?: string | null;
  stage?: string | null;
  model_version?: string | null;
  demo?: boolean | null;
  prediction_generated_at_utc?: string | null;
}
