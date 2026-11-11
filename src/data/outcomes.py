"""Shared retirement labels; unknown outcomes and causes remain unknown."""

import numpy as np
import pandas as pd


def dnf_labels(frame):
    status = frame.get("Status", pd.Series(pd.NA, index=frame.index)).astype("string").str.strip().str.lower()
    finished = status.str.fullmatch(r"finished|lapped|\+\d+ laps?", na=False)
    excluded = status.str.contains(
        r"disqualif|excluded|did not start|did not qualify|did not prequalify|withdr[ae]w|not started",
        na=False)
    collision_status = status.str.contains(r"collision|accident|spun", na=False)
    mechanical_status = status.str.contains(
        r"engine|gearbox|transmission|hydraul|electri|brake|suspension|power|oil|water|fuel|clutch|"
        r"driveshaft|radiator|overheat|technical|mechanical|turbo|exhaust|battery|differential|steering|puncture|tyre",
        na=False)
    classification = frame.get("ClassifiedPosition", pd.Series("", index=frame.index)).astype("string").str.upper()
    retired = (collision_status | mechanical_status | status.eq("retired").fillna(False)
               | classification.isin(["R", "RETIRED"])) & ~finished & ~excluded
    known = finished | retired

    def explicit(column):
        labels = pd.to_numeric(frame.get(column, pd.Series(np.nan, index=frame.index)), errors="coerce").astype(float)
        if not labels.dropna().isin([0, 1]).all():
            raise ValueError(f"{column} labels must be 0, 1 or missing")
        return labels

    total = explicit("target_dnf").fillna(retired.astype(float).where(known))
    cause_known = finished | collision_status | mechanical_status
    mechanical = explicit("target_dnf_mechanical").fillna(mechanical_status.astype(float).where(cause_known))
    collision = explicit("target_dnf_collision").fillna(collision_status.astype(float).where(cause_known))
    mechanical = mechanical.fillna(total.where(total.eq(0)))
    collision = collision.fillna(total.where(total.eq(0)))
    unidentified = total.eq(1) & mechanical.eq(0) & collision.eq(0)
    mechanical = mechanical.mask(unidentified)
    collision = collision.mask(unidentified)
    inferred = (mechanical.fillna(0) + collision.fillna(0)).clip(0, 1)
    total = total.fillna(inferred.where((mechanical.notna() & collision.notna()) | inferred.eq(1)))
    return total.mask(excluded), mechanical.mask(excluded), collision.mask(excluded)
