import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import unittest

from app.db.migrations import migrate_database
from app.operations.backup import BackupError, create_backup, restore_backup, validate_backup


class BackupRestoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.data.mkdir()
        migrate_database(self.data / "media_router.db")
        with sqlite3.connect(self.data / "media_router.db") as conn:
            conn.execute(
                "INSERT INTO providers VALUES ('provider-1','Provider','IPTV','notes',1,'Healthy','2026-01-01','2026-01-01')"
            )
            conn.execute(
                """INSERT INTO accounts
                (id,provider_id,friendly_name,username,password_secret,base_url,playlist_url,
                 max_simultaneous_streams,priority_group,weight,enabled,health_status,notes,created_at,updated_at)
                VALUES ('account-1','provider-1','Account','user','credential','https://provider.invalid','',2,
                        'Preferred',90,1,'Healthy','','2026-01-01','2026-01-01')"""
            )
            conn.execute(
                """INSERT INTO catalog_items
                (internal_id,media_type,title,normalized_title,confidence,created_at,updated_at)
                VALUES ('movie-stable','movie','Stable Movie','stable movie','high','2026-01-01','2026-01-01')"""
            )
        (self.data / "settings.json").write_text('{"app_name":"Restorable Router"}\n')
        (self.data / "emby_integration_settings.json").write_text(
            '{"enabled":true,"api_key":"sensitive-value"}\n'
        )
        (self.data / ".playback_ticket_secret").write_text("ticket-secret-value")
        os.chmod(self.data / ".playback_ticket_secret", 0o600)
        (self.data / "emby_integration_status.json").write_text('{"health_state":"ready"}\n')
        (self.data / "unrecognized.tmp").write_text("disposable")
        self.archive = self.root / "backup.tar.gz"

    def tearDown(self):
        self.temp.cleanup()

    def test_create_validate_and_disposable_restore_preserve_authoritative_state(self):
        manifest = create_backup(self.data, self.archive)
        self.assertEqual(0o600, self.archive.stat().st_mode & 0o777)
        self.assertTrue(manifest["components"]["playback_ticket_secret"])
        self.assertFalse(manifest["components"]["generated_outputs"])
        self.assertNotIn("data/emby_integration_status.json", manifest["files"])
        self.assertNotIn("data/unrecognized.tmp", manifest["files"])
        # Sensitive values are contained only in protected payload files, never
        # copied into the manifest.
        self.assertNotIn("sensitive-value", json.dumps(manifest))
        self.assertNotIn("ticket-secret-value", json.dumps(manifest))

        validated = validate_backup(self.archive)
        self.assertEqual(manifest["files"], validated["files"])
        restore = self.root / "restored"
        restore_backup(self.archive, restore)
        with sqlite3.connect(restore / "media_router.db") as conn:
            self.assertEqual(
                ("movie-stable", "Stable Movie"),
                conn.execute("SELECT internal_id,title FROM catalog_items").fetchone(),
            )
            self.assertEqual(
                ("user", "credential", 2),
                conn.execute("SELECT username,password_secret,max_simultaneous_streams FROM accounts").fetchone(),
            )
            self.assertEqual("ok", conn.execute("PRAGMA integrity_check").fetchone()[0])
        self.assertEqual((self.data / "settings.json").read_bytes(), (restore / "settings.json").read_bytes())
        self.assertEqual(
            (self.data / "emby_integration_settings.json").read_bytes(),
            (restore / "emby_integration_settings.json").read_bytes(),
        )
        self.assertEqual("ticket-secret-value", (restore / ".playback_ticket_secret").read_text())
        self.assertFalse((restore / "emby_integration_status.json").exists())
        self.assertTrue(all(path.stat().st_mode & 0o077 == 0 for path in restore.iterdir()))

    def test_restore_dry_run_does_not_create_destination(self):
        create_backup(self.data, self.archive)
        destination = self.root / "dry-run-target"
        restore_backup(self.archive, destination, dry_run=True)
        self.assertFalse(destination.exists())

    def test_create_refuses_overwrite_and_invalid_json(self):
        create_backup(self.data, self.archive)
        with self.assertRaisesRegex(BackupError, "refusing to overwrite"):
            create_backup(self.data, self.archive)
        second = self.root / "second.tar.gz"
        (self.data / "settings.json").write_text("not-json")
        with self.assertRaisesRegex(BackupError, "JSON state is unreadable"):
            create_backup(self.data, second)

    def test_validation_rejects_permissive_or_unexpected_archive(self):
        create_backup(self.data, self.archive)
        os.chmod(self.archive, 0o644)
        with self.assertRaisesRegex(BackupError, "permissions"):
            validate_backup(self.archive)
        os.chmod(self.archive, 0o600)

        unsafe = self.root / "unsafe.tar.gz"
        with tarfile.open(unsafe, "w:gz") as archive:
            info = tarfile.TarInfo("../escape")
            payload = b"unsafe"
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
        os.chmod(unsafe, 0o600)
        with self.assertRaisesRegex(BackupError, "unsafe or unexpected member"):
            validate_backup(unsafe)

    def test_restore_refuses_nonempty_destination(self):
        create_backup(self.data, self.archive)
        destination = self.root / "occupied"
        destination.mkdir()
        (destination / "keep.txt").write_text("keep")
        with self.assertRaisesRegex(BackupError, "must not exist or must be an empty"):
            restore_backup(self.archive, destination)
        self.assertEqual("keep", (destination / "keep.txt").read_text())

    def test_cli_launcher_uses_configured_python_runtime(self):
        create_backup(self.data, self.archive)
        script = Path(__file__).resolve().parents[1] / "scripts" / "media-router-backup"
        environment = os.environ.copy()
        environment["PYTHON"] = sys.executable

        result = subprocess.run(
            [str(script), "validate", str(self.archive)],
            capture_output=True,
            text=True,
            env=environment,
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn('"format_version": 1', result.stdout)


if __name__ == "__main__":
    unittest.main()
