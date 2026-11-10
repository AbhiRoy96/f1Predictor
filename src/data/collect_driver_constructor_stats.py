from pathlib import Path
import pandas as pd

RAW = Path("data/raw")
PROCESSED = Path("data/processed")

PROCESSED.mkdir(parents=True, exist_ok=True)


def collect_driver_constructor_stats():
    PROCESSED.mkdir(parents=True, exist_ok=True)

    files = list(RAW.glob("*_results.parquet"))

    if not files:
        print("No session result files found; existing driver and constructor lists were left unchanged.")
        return False

    frames: list[pd.DataFrame] = []
    for file in files:
        frame = pd.read_parquet(file)
        if "SessionType" not in frame.columns:
            name = file.stem.lower()
            if "sprint_qualifying" in name or "sprint_shootout" in name:
                session_type = "SQ"
            elif "qualifying" in name:
                session_type = "Q"
            elif "sprint" in name:
                session_type = "S"
            else:
                session_type = "R"
            frame["SessionType"] = session_type
        frames.append(frame)

    results: pd.DataFrame = pd.concat(frames, ignore_index=True, sort=False)

    driver_columns = [
        "Abbreviation",
        "DriverNumber",
        "FirstName",
        "LastName",
        "FullName",
    ]

    constructor_columns = [
        "TeamName",
        "TeamColor",
    ]

    available_driver = [
        c for c in driver_columns if c in results.columns
    ]

    available_team = [
        c for c in constructor_columns if c in results.columns
    ]

    if "Abbreviation" in available_driver:
        drivers = (
            results[available_driver]
            .dropna(subset=["Abbreviation"])
            .drop_duplicates()
        )
    else:
        drivers = pd.DataFrame(columns=available_driver)

    if "TeamName" in available_team:
        constructors = (
            results[available_team]
            .dropna(subset=["TeamName"])
            .drop_duplicates()
        )
    else:
        constructors = pd.DataFrame(columns=available_team)

    drivers.to_csv(
        PROCESSED / "drivers.csv",
        index=False
    )

    constructors.to_csv(
        PROCESSED / "constructors.csv",
        index=False
    )

    print(f"Drivers: {len(drivers)}")
    print(f"Constructors: {len(constructors)}")
    return True