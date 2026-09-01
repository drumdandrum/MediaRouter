import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from app.core.config import get_settings
from app.db.migrations import migrate_database
from app.schemas.providers import AccountCreate, ProviderCreate
from app.services import broker, catalog, emby, outputs, providers


class RequestSchemaBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        os.environ["MEDIA_ROUTER_DATA_DIR"] = str(Path(self.temp.name) / "data")
        get_settings.cache_clear()
        migrate_database()

    def tearDown(self):
        get_settings.cache_clear()
        os.environ.pop("MEDIA_ROUTER_DATA_DIR", None)
        self.temp.cleanup()

    def test_representative_runtime_reads_and_writes_issue_no_schema_ddl(self):
        real_connect = sqlite3.connect
        statements: list[str] = []

        def traced_connect(*args, **kwargs):
            connection = real_connect(*args, **kwargs)
            connection.set_trace_callback(statements.append)
            return connection

        with patch.object(sqlite3, "connect", side_effect=traced_connect):
            self.assertEqual(0, catalog.get_summary().total_items)
            provider = providers.create_provider(
                ProviderCreate(friendly_name="Runtime Provider", provider_type="IPTV")
            )
            account = providers.create_account(
                AccountCreate(provider_id=provider.id, friendly_name="Runtime Account")
            )
            self.assertIsNotNone(account)
            self.assertEqual(1, len(providers.list_accounts()))
            self.assertEqual(0, broker.get_status().total_reservations)
            self.assertEqual([], outputs.list_output_history(limit=1))
            self.assertEqual([], emby.list_emby_channel_mappings())

        ddl_prefixes = ("CREATE ", "ALTER ", "DROP ", "REINDEX ", "VACUUM")
        ddl = [statement for statement in statements if statement.lstrip().upper().startswith(ddl_prefixes)]
        migration_writes = [
            statement for statement in statements
            if "SCHEMA_MIGRATIONS" in statement.upper()
            and statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
        ]
        self.assertEqual([], ddl)
        self.assertEqual([], migration_writes)

    def test_missing_schema_is_reported_instead_of_repaired_by_request(self):
        empty_data = Path(self.temp.name) / "missing-schema"
        os.environ["MEDIA_ROUTER_DATA_DIR"] = str(empty_data)
        get_settings.cache_clear()

        with self.assertRaisesRegex(sqlite3.OperationalError, "no such table"):
            catalog.get_summary()

        db = empty_data / "media_router.db"
        with sqlite3.connect(db) as connection:
            self.assertEqual(
                [],
                connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ).fetchall(),
            )


if __name__ == "__main__":
    unittest.main()
