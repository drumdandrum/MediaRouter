import os
from pathlib import Path
import sqlite3
import tempfile
import time
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

    def test_connect_initialization_failure_rolls_back_closes_and_reraises(self):
        cases = (
            (catalog, "ensure_schema"),
            (providers, "ensure_provider_schema"),
            (outputs, "ensure_schema"),
        )
        for module, initializer in cases:
            with self.subTest(module=module.__name__):
                connection = MagicMock()
                primary = RuntimeError(f"{module.__name__} initialization failed")
                with (
                    patch.object(module.sqlite3, "connect", return_value=connection),
                    patch.object(module, initializer, side_effect=primary),
                ):
                    with self.assertRaises(RuntimeError) as raised:
                        module._connect()
                self.assertIs(raised.exception, primary)
                connection.rollback.assert_called_once_with()
                connection.close.assert_called_once_with()

        connection = MagicMock()
        primary = RuntimeError("broker initialization failed")
        with (
            patch.object(broker.sqlite3, "connect", return_value=connection),
            patch.object(catalog, "ensure_schema", side_effect=primary),
        ):
            with self.assertRaises(RuntimeError) as raised:
                broker._connect()
        self.assertIs(raised.exception, primary)
        connection.rollback.assert_called_once_with()
        connection.close.assert_called_once_with()

        connection = MagicMock()
        primary = RuntimeError("emby initialization failed")
        with (
            patch("app.services.broker._connect", return_value=connection),
            patch.object(emby, "ensure_emby_schema", side_effect=primary),
        ):
            with self.assertRaises(RuntimeError) as raised:
                emby._connect()
        self.assertIs(raised.exception, primary)
        connection.rollback.assert_called_once_with()
        connection.close.assert_called_once_with()

    def test_cleanup_failure_does_not_mask_initialization_failure(self):
        connection = MagicMock()
        connection.rollback.side_effect = KeyboardInterrupt("rollback cleanup interrupted")
        connection.close.side_effect = SystemExit("close cleanup interrupted")
        primary = ValueError("primary initialization failure")
        with (
            patch.object(catalog.sqlite3, "connect", return_value=connection),
            patch.object(catalog, "ensure_schema", side_effect=primary),
        ):
            with self.assertRaises(ValueError) as raised:
                catalog._connect()
        self.assertIs(raised.exception, primary)
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

    def test_commit_lock_failure_does_not_retain_writer_lock(self):
        db_path = Path(self.temp.name) / "lock-regression.db"
        setup = sqlite3.connect(db_path)
        setup.execute("CREATE TABLE probe (value INTEGER)")
        setup.commit()
        setup.close()

        blocker = sqlite3.connect(db_path)
        blocker.execute("BEGIN")
        blocker.execute("SELECT * FROM probe").fetchall()

        real_connect = sqlite3.connect
        failed_connections = []

        def short_connect(*args, **kwargs):
            connection = real_connect(*args, timeout=0.05, **kwargs)
            failed_connections.append(connection)
            return connection

        def write_then_commit(connection):
            connection.execute("INSERT INTO probe(value) VALUES (1)")
            connection.commit()

        started = time.monotonic()
        with (
            patch.object(catalog, "_db_path", return_value=db_path),
            patch.object(catalog.sqlite3, "connect", side_effect=short_connect),
            patch.object(catalog, "ensure_schema", side_effect=write_then_commit),
        ):
            with self.assertRaisesRegex(sqlite3.OperationalError, "locked"):
                catalog._connect()
        self.assertLess(time.monotonic() - started, 1)
        self.assertEqual(1, len(failed_connections))
        with self.assertRaises(sqlite3.ProgrammingError):
            failed_connections[0].execute("SELECT 1")

        blocker.rollback()
        blocker.close()

        third = real_connect(db_path, timeout=0.05)
        try:
            third.execute("INSERT INTO probe(value) VALUES (2)")
            third.commit()
        finally:
            third.close()

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
