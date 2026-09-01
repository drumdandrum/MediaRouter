import os
from datetime import datetime
from pathlib import Path
import sqlite3
import tempfile
import unittest

from app.core.config import get_settings
from app.services.catalog import ensure_schema, get_summary


class DashboardCatalogMetricTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        os.environ["MEDIA_ROUTER_DATA_DIR"] = str(Path(self.temp.name) / "data")
        get_settings.cache_clear()
        ensure_schema()

    def tearDown(self):
        get_settings.cache_clear()
        os.environ.pop("MEDIA_ROUTER_DATA_DIR", None)
        self.temp.cleanup()

    def test_summary_separates_unique_items_from_source_rows(self):
        now = datetime.utcnow().isoformat()
        db = get_settings().data_dir / "media_router.db"
        with sqlite3.connect(db) as conn:
            for item_id, media_type in (
                ("channel_one", "channel"),
                ("movie_one", "movie"),
                ("series_one", "series"),
                ("episode_one", "episode"),
            ):
                conn.execute(
                    """INSERT INTO catalog_items
                    (internal_id,media_type,title,normalized_title,confidence,created_at,updated_at)
                    VALUES (?,?,?,?, 'high',?,?)""",
                    (item_id, media_type, item_id, item_id, now, now),
                )
            for index in range(3):
                conn.execute(
                    """INSERT INTO source_availability
                    (catalog_internal_id,location_ref,media_type,enabled,last_seen_at,created_at,updated_at)
                    VALUES ('movie_one',?,'movie',1,?,?,?)""",
                    (f"https://provider.invalid/movie/{index}", now, now, now),
                )

        summary = get_summary()

        self.assertEqual((summary.channels, summary.movies, summary.series, summary.episodes), (1, 1, 1, 1))
        self.assertEqual(summary.total_items, 4)
        self.assertEqual(summary.sources, 3)

    def test_dashboard_uses_item_total_and_labels_source_rows(self):
        javascript = Path("app/static/app.js").read_text(encoding="utf-8")
        template = Path("app/templates/index.html").read_text(encoding="utf-8")

        self.assertIn('data.catalog_items', javascript)
        self.assertNotIn('document.getElementById("catalog-count").textContent = data.catalog_sources', javascript)
        self.assertIn('<span>Catalog Items</span><strong id="catalog-count">', template)
        self.assertIn('<span>Source Rows</span><strong id="cat-summary-sources">', template)


if __name__ == "__main__":
    unittest.main()
