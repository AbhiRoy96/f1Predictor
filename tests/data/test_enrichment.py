"""Unit tests for Wikipedia and Wikimedia Commons enrichment."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.enrichment.commons_client import CommonsClient
from src.enrichment.media_metadata import MediaMetadata, load_metadata_cache, save_metadata_cache
from src.enrichment.wikipedia_client import WikipediaClient


class TestEnrichment(unittest.TestCase):
    def test_media_metadata_schema_and_cache(self):
        meta = MediaMetadata(
            entity_type="driver",
            entity_id="VER",
            display_name="Max Verstappen",
            wikipedia_url="https://en.wikipedia.org/wiki/Max_Verstappen",
            summary="Dutch racing driver and World Champion.",
            image_url="https://upload.wikimedia.org/verstappen.jpg",
            license="CC BY-SA 4.0",
            attribution="Wikimedia Commons contributors",
        )
        self.assertEqual(meta.entity_type, "driver")
        self.assertEqual(meta.entity_id, "VER")

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / "test_cache.json"
            save_metadata_cache({"driver_VER": meta}, cache_file)

            loaded = load_metadata_cache(cache_file)
            self.assertIn("driver_VER", loaded)
            self.assertEqual(loaded["driver_VER"].display_name, "Max Verstappen")

    @patch("requests.Session.get")
    def test_wikipedia_client_mock_fetch(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "extract": "Max Verstappen is a Dutch racing driver.",
            "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Max_Verstappen"}},
            "thumbnail": {"source": "https://example.com/ver.jpg"},
        }
        mock_get.return_value = mock_resp

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / "wiki_test.json"
            client = WikipediaClient(cache_file=cache_file)
            res = client.get_driver_info("VER")

            self.assertEqual(res.display_name, "Max Verstappen")
            self.assertEqual(res.summary, "Max Verstappen is a Dutch racing driver.")
            self.assertEqual(res.image_url, "https://example.com/ver.jpg")


if __name__ == "__main__":
    unittest.main()
