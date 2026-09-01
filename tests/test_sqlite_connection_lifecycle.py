import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from app.core.config import get_settings
from app.services import broker, catalog, emby, outputs, providers
from app.services.sqlite_connection import rollback_and_close


class _TrackingConnection(sqlite3.Connection):
    live_count = 0
    opened_count = 0
    closed_count = 0

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        type(self).live_count += 1
        type(self).opened_count += 1
        self._tracked_closed = False

    def close(self):
        if not self._tracked_closed:
            self._tracked_closed = True
            type(self).live_count -= 1
            type(self).closed_count += 1
        return super().close()

    @classmethod
    def reset(cls):
        cls.live_count = 0
        cls.opened_count = 0
        cls.closed_count = 0


class SQLiteConnectionLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        os.environ["MEDIA_ROUTER_DATA_DIR"] = str(Path(self.temp.name) / "data")
        get_settings.cache_clear()

    def tearDown(self):
        get_settings.cache_clear()
        os.environ.pop("MEDIA_ROUTER_DATA_DIR", None)
        self.temp.cleanup()

    def test_runtime_connectors_do_not_invoke_schema_initializers(self):
        cases = (
            (catalog, "ensure_schema"),
            (providers, "ensure_provider_schema"),
            (outputs, "ensure_outputs_schema"),
            (broker, "ensure_broker_schema"),
        )
        for module, initializer in cases:
            with self.subTest(module=module.__name__):
                connection = MagicMock()
                with (
                    patch.object(module.sqlite3, "connect", return_value=connection),
                    patch.object(module, initializer) as schema_initializer,
                ):
                    opened = module._connect()
                self.assertIs(connection, opened)
                schema_initializer.assert_not_called()

        connection = MagicMock()
        with (
            patch("app.services.broker._connect", return_value=connection),
            patch.object(emby, "ensure_emby_schema") as schema_initializer,
        ):
            opened = emby._connect()
        self.assertIs(connection, opened)
        schema_initializer.assert_not_called()

    def test_connection_configuration_failure_rolls_back_closes_and_reraises(self):
        connection = MagicMock()
        primary = sqlite3.OperationalError("synthetic pragma failure")
        connection.execute.side_effect = primary
        with patch.object(catalog.sqlite3, "connect", return_value=connection):
            with self.assertRaises(sqlite3.OperationalError) as raised:
                catalog._connect()
        self.assertIs(primary, raised.exception)
        connection.rollback.assert_called_once_with()
        connection.close.assert_called_once_with()

    def test_rollback_and_close_attempts_both_cleanup_operations(self):
        connection = MagicMock()
        connection.rollback.side_effect = RuntimeError("rollback failed")
        connection.close.side_effect = RuntimeError("close failed")
        rollback_and_close(connection)
        connection.rollback.assert_called_once_with()
        connection.close.assert_called_once_with()

    def test_caller_owned_schema_connection_remains_open_and_commit_contract_holds(self):
        db_path = get_settings().data_dir / "media_router.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(db_path)
        try:
            catalog.ensure_schema(connection)
            self.assertFalse(connection.in_transaction)
            self.assertEqual(
                1,
                connection.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE name='catalog_items'"
                ).fetchone()[0],
            )
        finally:
            connection.close()

    def test_caller_owned_schema_connection_is_not_closed_or_rolled_back_on_failure(self):
        db_path = get_settings().data_dir / "media_router.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(db_path)
        connection.execute("CREATE TABLE caller_owned_probe (value INTEGER)")
        connection.commit()
        connection.execute("INSERT INTO caller_owned_probe(value) VALUES (1)")
        primary = RuntimeError("caller-owned schema failure")
        try:
            with patch.object(catalog, "_ensure_schema", side_effect=primary):
                with self.assertRaises(RuntimeError) as raised:
                    catalog.ensure_schema(connection)
            self.assertIs(raised.exception, primary)
            self.assertTrue(connection.in_transaction)
            self.assertEqual(
                1,
                connection.execute("SELECT COUNT(*) FROM caller_owned_probe").fetchone()[0],
            )
        finally:
            connection.rollback()
            connection.close()

    def test_owned_schema_connection_closes_on_success_and_failure(self):
        real_connect = sqlite3.connect

        def tracked_connect(*args, **kwargs):
            return real_connect(*args, factory=_TrackingConnection, **kwargs)

        get_settings().data_dir.mkdir(parents=True, exist_ok=True)
        cases = (
            (providers, "ensure_provider_schema", "_ensure_provider_schema"),
            (catalog, "ensure_schema", "_ensure_schema"),
            (outputs, "ensure_outputs_schema", "_ensure_outputs_schema"),
            (broker, "ensure_broker_schema", "_ensure_broker_schema"),
            (emby, "ensure_emby_schema", "_ensure_emby_schema"),
        )
        for module, public_name, implementation_name in cases:
            with self.subTest(module=module.__name__, outcome="success"):
                _TrackingConnection.reset()
                with patch.object(module.sqlite3, "connect", side_effect=tracked_connect):
                    getattr(module, public_name)()
                self.assertEqual(0, _TrackingConnection.live_count)
                self.assertEqual(1, _TrackingConnection.opened_count)
                self.assertEqual(1, _TrackingConnection.closed_count)

            with self.subTest(module=module.__name__, outcome="failure"):
                _TrackingConnection.reset()
                primary = RuntimeError(f"{module.__name__} owned schema failure")
                with (
                    patch.object(module.sqlite3, "connect", side_effect=tracked_connect),
                    patch.object(module, implementation_name, side_effect=primary),
                ):
                    with self.assertRaises(RuntimeError) as raised:
                        getattr(module, public_name)()
                self.assertIs(raised.exception, primary)
                self.assertEqual(0, _TrackingConnection.live_count)
                self.assertEqual(1, _TrackingConnection.opened_count)
                self.assertEqual(1, _TrackingConnection.closed_count)

    def test_representative_read_paths_close_every_connection(self):
        real_connect = sqlite3.connect

        def tracked_connect(*args, **kwargs):
            return real_connect(*args, factory=_TrackingConnection, **kwargs)

        _TrackingConnection.reset()
        with patch.object(sqlite3, "connect", side_effect=tracked_connect):
            catalog.ensure_schema()
            broker.ensure_broker_schema()
            outputs.ensure_outputs_schema()
            emby.ensure_emby_schema()
            for _ in range(100):
                catalog.get_summary()
                providers.list_providers()
                outputs.list_output_history(limit=1)
                broker.get_status()
                emby.get_emby_status()

        self.assertEqual(0, _TrackingConnection.live_count)
        self.assertEqual(_TrackingConnection.opened_count, _TrackingConnection.closed_count)
        self.assertGreaterEqual(_TrackingConnection.opened_count, 500)


if __name__ == "__main__":
    unittest.main()
