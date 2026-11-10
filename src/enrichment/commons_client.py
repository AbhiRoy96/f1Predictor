"""Wikimedia Commons API client for free-licensed motorsport media assets and attribution."""

from pathlib import Path
from typing import Dict, Optional
import requests
from src.enrichment.media_metadata import MediaMetadata, load_metadata_cache, save_metadata_cache
from src.utils.config import CACHE_DIR
from src.utils.logging import setup_logger

logger = setup_logger(__name__)

COMMONS_CACHE_FILE = CACHE_DIR / "commons.json"
USER_AGENT = "F1RaceIntelligenceBot/1.0 (https://github.com/f1-race-intelligence; contact@example.com)"


class CommonsClient:
    """Queries Wikimedia Commons API for media files, authors, and license attribution."""

    def __init__(self, cache_file: Path = COMMONS_CACHE_FILE, timeout: int = 5):
        self.cache_file = cache_file
        self.timeout = timeout
        self.cache = load_metadata_cache(self.cache_file)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})

    def search_image(self, query: str) -> Optional[Dict[str, str]]:
        """Search Wikimedia Commons for an image and return its URL and attribution."""
        cache_key = f"commons_{query.lower().replace(' ', '_')}"
        if cache_key in self.cache:
            meta = self.cache[cache_key]
            return {
                "image_url": meta.image_url,
                "file_page": meta.commons_file_page,
                "creator": meta.creator,
                "license": meta.license,
                "attribution": meta.attribution,
            }

        url = "https://commons.wikimedia.org/w/api.php"
        params = {
            "action": "query",
            "generator": "search",
            "gsrsearch": f"File:{query}",
            "gsrnamespace": "6",  # File namespace
            "gsrlimit": "1",
            "prop": "imageinfo",
            "iiprop": "url|user|extmetadata",
            "format": "json",
        }

        try:
            resp = self.session.get(url, params=params, timeout=self.timeout)
            if resp.status_code == 200:
                data = resp.json()
                pages = data.get("query", {}).get("pages", {})
                for page_id, page in pages.items():
                    imageinfo = page.get("imageinfo", [{}])[0]
                    img_url = imageinfo.get("url", "")
                    desc_url = imageinfo.get("descriptionurl", "")
                    user = imageinfo.get("user", "Wikimedia Commons user")
                    extmeta = imageinfo.get("extmetadata", {})
                    license_name = extmeta.get("LicenseShortName", {}).get("value", "CC BY-SA")
                    artist = extmeta.get("Artist", {}).get("value", user)

                    meta = MediaMetadata(
                        entity_type="media",
                        entity_id=query,
                        display_name=query,
                        image_url=img_url,
                        commons_file_page=desc_url,
                        creator=artist,
                        license=license_name,
                        attribution=f"Photo by {artist} via Wikimedia Commons ({license_name})",
                    )
                    self.cache[cache_key] = meta
                    save_metadata_cache(self.cache, self.cache_file)
                    return {
                        "image_url": img_url,
                        "file_page": desc_url,
                        "creator": artist,
                        "license": license_name,
                        "attribution": meta.attribution,
                    }
        except Exception as e:
            logger.debug(f"Commons search error for {query}: {e}")

        return None
