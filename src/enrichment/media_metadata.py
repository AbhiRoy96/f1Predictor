"""Metadata schemas and persistence for Wikipedia and Wikimedia Commons media assets."""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Dict, Optional


@dataclass
class MediaMetadata:
    """Schema matching the specification in README Media Metadata Schema."""

    entity_type: str  # driver, constructor, circuit, race
    entity_id: str  # stable identifier (e.g. "VER", "Ferrari")
    display_name: str
    wikipedia_url: str = ""
    summary: str = ""
    image_url: str = ""
    commons_file_page: str = ""
    creator: str = "Unknown"
    license: str = "Creative Commons"
    attribution: str = ""
    retrieved_at: str = ""

    def __post_init__(self):
        if not self.retrieved_at:
            self.retrieved_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MediaMetadata":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


def save_metadata_cache(cache: Dict[str, MediaMetadata], cache_file: Path) -> None:
    """Save metadata dictionary to a JSON cache file."""
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    serializable = {k: v.to_dict() for k, v in cache.items()}
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(serializable, f, indent=2, ensure_ascii=False)


def load_metadata_cache(cache_file: Path) -> Dict[str, MediaMetadata]:
    """Load metadata dictionary from a JSON cache file."""
    if not cache_file.exists():
        return {}
    try:
        with open(cache_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            return {k: MediaMetadata.from_dict(v) for k, v in data.items()}
    except Exception:
        return {}
