"""Version-checked persistence for trusted, repository-generated joblib artifacts.

Joblib is pickle-based: version validation does not make untrusted files safe.
"""

from importlib.metadata import version
import platform

import joblib

LIBRARIES = ("fastf1", "numpy", "pandas", "scipy", "scikit-learn", "xgboost",
             "joblib", "pyarrow", "requests")


def library_versions():
    return {"python": platform.python_version(),
            **{name: version(name) for name in LIBRARIES}}


def validate_library_versions(metadata):
    stored = metadata.get("library_versions")
    current = library_versions()
    if not isinstance(stored, dict) or stored != current:
        raise ValueError("Incompatible model library versions (or missing version metadata); retrain "
                         f"with this environment. Saved={stored}, runtime={current}")


def dump_artifact(artifact, path):
    artifact.setdefault("metadata", {})["library_versions"] = library_versions()
    return joblib.dump(artifact, path)


def load_artifact(path):
    artifact = joblib.load(path)
    if not isinstance(artifact, dict):
        raise ValueError("Model artifact must contain versioned metadata; retrain it.")
    validate_library_versions(artifact.get("metadata", {}))
    return artifact
