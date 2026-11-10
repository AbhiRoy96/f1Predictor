"""Entity normalization and alias mapping for drivers, constructors, and circuits."""

from typing import Dict, Optional

# Constructor historical aliases to canonical modern or unified identities
CONSTRUCTOR_ALIASES: Dict[str, str] = {
    "red bull": "Red Bull Racing",
    "red bull racing": "Red Bull Racing",
    "ferrari": "Ferrari",
    "scuderia ferrari": "Ferrari",
    "mercedes": "Mercedes",
    "mercedes-amg": "Mercedes",
    "mclaren": "McLaren",
    "aston martin": "Aston Martin",
    "racing point": "Aston Martin",
    "force india": "Aston Martin",
    "sahara force india": "Aston Martin",
    "alpine": "Alpine",
    "renault": "Alpine",
    "williams": "Williams",
    "alfa romeo": "Kick Sauber",
    "sauber": "Kick Sauber",
    "kick sauber": "Kick Sauber",
    "stake f1 team kick sauber": "Kick Sauber",
    "alphatauri": "RB",
    "alpha tauri": "RB",
    "scuderia alphatauri": "RB",
    "toro rosso": "RB",
    "scuderia toro rosso": "RB",
    "rb": "RB",
    "visa cash app rb": "RB",
    "haas": "Haas",
    "haas f1 team": "Haas",
}

# Driver abbreviations to full names
DRIVER_NAMES: Dict[str, str] = {
    "VER": "Max Verstappen",
    "HAM": "Lewis Hamilton",
    "NOR": "Lando Norris",
    "LEC": "Charles Leclerc",
    "PIA": "Oscar Piastri",
    "SAI": "Carlos Sainz",
    "RUS": "George Russell",
    "PER": "Sergio Perez",
    "ALO": "Fernando Alonso",
    "STR": "Lance Stroll",
    "TSU": "Yuki Tsunoda",
    "RIC": "Daniel Ricciardo",
    "LAW": "Liam Lawson",
    "ALB": "Alexander Albon",
    "COL": "Franco Colapinto",
    "OCO": "Esteban Ocon",
    "GAS": "Pierre Gasly",
    "HUL": "Nico Hulkenberg",
    "MAG": "Kevin Magnussen",
    "BOT": "Valtteri Bottas",
    "ZHO": "Zhou Guanyu",
    "BEA": "Oliver Bearman",
    "ANT": "Kimi Antonelli",
    "DOO": "Jack Doohan",
    "BOR": "Gabriel Bortoleto",
    "HAD": "Isack Hadjar",
}

# Circuit aliases to canonical circuit display names
CIRCUIT_ALIASES: Dict[str, str] = {
    "interlagos": "Autódromo José Carlos Pace",
    "autódromo josé carlos pace": "Autódromo José Carlos Pace",
    "brazil": "Autódromo José Carlos Pace",
    "brazilian grand prix": "Autódromo José Carlos Pace",
    "são paulo grand prix": "Autódromo José Carlos Pace",
    "sao paulo grand prix": "Autódromo José Carlos Pace",
    "silverstone": "Silverstone Circuit",
    "monza": "Autodromo Nazionale Monza",
    "spa": "Circuit de Spa-Francorchamps",
    "spa-francorchamps": "Circuit de Spa-Francorchamps",
    "monaco": "Circuit de Monaco",
    "suzuka": "Suzuka International Racing Course",
    "bahrain": "Bahrain International Circuit",
    "jeddah": "Jeddah Corniche Circuit",
    "albert park": "Albert Park Grand Prix Circuit",
    "melbourne": "Albert Park Grand Prix Circuit",
    "austin": "Circuit of the Americas",
    "cota": "Circuit of the Americas",
    "yas marina": "Yas Marina Circuit",
    "abu dhabi": "Yas Marina Circuit",
}


def normalise_constructor(name: Optional[str]) -> str:
    """Normalize constructor name to canonical name."""
    if not name or str(name).strip() == "" or str(name).lower() == "nan":
        return "Unknown Team"
    clean = str(name).strip().casefold()
    return CONSTRUCTOR_ALIASES.get(clean, str(name).strip())


def normalise_driver_name(abbreviation: Optional[str]) -> str:
    """Resolve full driver name from 3-letter abbreviation."""
    if not abbreviation:
        return "Unknown Driver"
    abbr = str(abbreviation).strip().upper()
    return DRIVER_NAMES.get(abbr, abbr)


def normalise_circuit(name: Optional[str]) -> str:
    """Normalize circuit name to canonical name."""
    if not name or str(name).strip() == "" or str(name).lower() == "nan":
        return "Unknown Circuit"
    clean = str(name).strip().casefold()
    return CIRCUIT_ALIASES.get(clean, str(name).strip())
