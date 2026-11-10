"""Wikipedia MediaWiki REST API client for entity descriptions and media links."""

from pathlib import Path
from typing import Dict, Optional
import requests
from src.data.normalise_entities import normalise_circuit, normalise_constructor, normalise_driver_name
from src.enrichment.media_metadata import MediaMetadata, load_metadata_cache, save_metadata_cache
from src.utils.config import CACHE_DIR
from src.utils.logging import setup_logger

logger = setup_logger(__name__)

USER_AGENT = "F1RaceIntelligenceBot/1.0 (https://github.com/f1-race-intelligence; contact@example.com)"
WIKI_CACHE_FILE = CACHE_DIR / "wikipedia.json"


class WikipediaClient:
    """Fetches and caches contextual summaries and images from Wikipedia."""

    def __init__(self, cache_file: Path = WIKI_CACHE_FILE, timeout: int = 5):
        self.cache_file = cache_file
        self.timeout = timeout
        self.cache = load_metadata_cache(self.cache_file)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})

    def get_driver_info(self, abbreviation: str) -> MediaMetadata:
        """Fetch summary and portrait for an F1 driver by abbreviation."""
        abbr = abbreviation.strip().upper()
        cache_key = f"driver_{abbr}"
        if cache_key in self.cache:
            return self.cache[cache_key]

        full_name = normalise_driver_name(abbr)
        search_title = full_name.replace(" ", "_")
        meta = self._fetch_wiki_summary("driver", abbr, full_name, search_title)
        self.cache[cache_key] = meta
        save_metadata_cache(self.cache, self.cache_file)
        return meta

    def get_constructor_info(self, team_name: str) -> MediaMetadata:
        """Fetch summary and details for an F1 constructor."""
        norm_team = normalise_constructor(team_name)
        cache_key = f"constructor_{norm_team}"
        if cache_key in self.cache:
            return self.cache[cache_key]

        search_title = f"{norm_team}_in_Formula_One".replace(" ", "_")
        meta = self._fetch_wiki_summary("constructor", norm_team, norm_team, search_title)
        if not meta.summary or meta.summary.startswith("Information unavailable"):
            # Try searching the plain team name
            meta = self._fetch_wiki_summary("constructor", norm_team, norm_team, norm_team.replace(" ", "_"))

        self.cache[cache_key] = meta
        save_metadata_cache(self.cache, self.cache_file)
        return meta

    def get_circuit_info(self, circuit_name: str) -> MediaMetadata:
        """Fetch circuit history and track description."""
        norm_circ = normalise_circuit(circuit_name)
        cache_key = f"circuit_{norm_circ}"
        if cache_key in self.cache:
            return self.cache[cache_key]

        search_title = norm_circ.replace(" ", "_")
        meta = self._fetch_wiki_summary("circuit", norm_circ, norm_circ, search_title)
        self.cache[cache_key] = meta
        save_metadata_cache(self.cache, self.cache_file)
        return meta

    def _fetch_wiki_summary(
        self,
        entity_type: str,
        entity_id: str,
        display_name: str,
        title: str,
    ) -> MediaMetadata:
        """Make GET request to Wikipedia REST API."""
        url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{title}"
        try:
            resp = self.session.get(url, timeout=self.timeout)
            if resp.status_code == 200:
                data = resp.json()
                summary = data.get("extract", "")
                page_url = data.get("content_urls", {}).get("desktop", {}).get("page", "")
                thumbnail = data.get("thumbnail", {}).get("source", "")
                return MediaMetadata(
                    entity_type=entity_type,
                    entity_id=entity_id,
                    display_name=display_name,
                    wikipedia_url=page_url,
                    summary=summary,
                    image_url=thumbnail,
                    attribution="Wikipedia contributors (CC BY-SA 4.0)",
                    license="CC BY-SA 4.0",
                )
        except Exception as e:
            logger.debug(f"Wikipedia fetch error for {title}: {e}")

        # Fallback metadata if API unreachable or not found
        return MediaMetadata(
            entity_type=entity_type,
            entity_id=entity_id,
            display_name=display_name,
            summary=f"Formula 1 {entity_type} {display_name}.",
            attribution="Formula 1 Intelligence",
        )
