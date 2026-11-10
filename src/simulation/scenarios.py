"""Race scenarios and environmental parameter configurations for Monte Carlo simulation."""

from dataclasses import dataclass
from typing import Dict


@dataclass
class RaceScenario:
    """Parameters defining a race simulation environment."""

    name: str
    description: str
    safety_car_probability: float = 0.25
    vsc_probability: float = 0.30
    weather_rain_probability: float = 0.05
    dnf_base_rate: float = 0.06
    pace_variance_scale: float = 1.0
    overtaking_difficulty: float = 0.5  # 0.0 = very easy, 1.0 = monaco-like hard
    tyre_deg_multiplier: float = 1.0
    pl_temperature: float = 1.0
    team_sigma: float = 0.0

    def __post_init__(self):
        from math import isfinite

        for field in ["safety_car_probability", "vsc_probability", "weather_rain_probability",
                      "dnf_base_rate", "overtaking_difficulty"]:
            if not 0 <= getattr(self, field) <= 1:
                raise ValueError(f"{field} must lie in [0, 1]")
        for field in ["pace_variance_scale", "tyre_deg_multiplier", "pl_temperature"]:
            if not isfinite(getattr(self, field)) or getattr(self, field) <= 0:
                raise ValueError(f"{field} must be finite and positive")
        if not isfinite(self.team_sigma) or self.team_sigma < 0:
            raise ValueError("team_sigma must be finite and nonnegative")


SCENARIOS: Dict[str, RaceScenario] = {
    "standard_dry": RaceScenario(
        name="Standard Dry Race",
        description="Dry track conditions, average safety car probability, standard reliability.",
        safety_car_probability=0.25,
        vsc_probability=0.30,
        weather_rain_probability=0.0,
        dnf_base_rate=0.05,
        pace_variance_scale=1.0,
        overtaking_difficulty=0.5,
        tyre_deg_multiplier=1.0,
    ),
    "wet_chaos": RaceScenario(
        name="Wet Weather Chaos",
        description="Rain showers, high safety car likelihood, elevated retirement rates, and higher pace variance.",
        safety_car_probability=0.75,
        vsc_probability=0.50,
        weather_rain_probability=0.90,
        dnf_base_rate=0.15,
        pace_variance_scale=2.2,
        overtaking_difficulty=0.35,
        tyre_deg_multiplier=0.8,
    ),
    "high_safety_car": RaceScenario(
        name="High Safety Car Intervention",
        description="Frequent incidents bunching the pack and compressing strategic gaps (e.g. Interlagos / Singapore).",
        safety_car_probability=0.80,
        vsc_probability=0.60,
        weather_rain_probability=0.15,
        dnf_base_rate=0.10,
        pace_variance_scale=1.4,
        overtaking_difficulty=0.45,
        tyre_deg_multiplier=1.1,
    ),
    "high_degradation": RaceScenario(
        name="High Tyre Degradation",
        description="Severe tyre wear causing multiple pit stop strategies and significant pace drops on worn tyres.",
        safety_car_probability=0.20,
        vsc_probability=0.25,
        weather_rain_probability=0.0,
        dnf_base_rate=0.05,
        pace_variance_scale=1.3,
        overtaking_difficulty=0.40,
        tyre_deg_multiplier=1.8,
    ),
    "reliability_attrition": RaceScenario(
        name="High Mechanical Attrition",
        description="Elevated powertrain and mechanical failure risks across the field.",
        safety_car_probability=0.35,
        vsc_probability=0.40,
        weather_rain_probability=0.05,
        dnf_base_rate=0.18,
        pace_variance_scale=1.1,
        overtaking_difficulty=0.5,
        tyre_deg_multiplier=1.0,
    ),
}


def get_scenario(scenario_name: str = "standard_dry") -> RaceScenario:
    """Retrieve an explicit sensitivity scenario; unknown names are errors."""
    key = scenario_name.lower()
    if key not in SCENARIOS:
        raise ValueError(f"Unknown scenario {scenario_name!r}; choose from {', '.join(SCENARIOS)}")
    return SCENARIOS[key]
