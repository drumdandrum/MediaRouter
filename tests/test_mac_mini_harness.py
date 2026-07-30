import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest

from app.services.catalog import parse_m3u_entries


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
from mac_mini_harness import (  # noqa: E402
    CONTAINER,
    HarnessError,
    PROJECT,
    ensure_feed_id,
    restore_archive,
    safe_destroy,
    safe_reset,
    validate_archive,
    validate_compose,
    validate_emby_target,
    validate_secret_permissions,
)


class MacMiniHarnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run([str(SCRIPTS / "mac-mini-test"), "init"], cwd=ROOT, check=True,
                       capture_output=True, text=True)

    def rendered_config(self):
        result = subprocess.run([
            "docker", "compose", "-p", PROJECT,
            "-f", str(ROOT / "docker-compose.yml"),
            "-f", str(ROOT / "deploy/mac-mini/compose.test.yml"),
            "--env-file", str(ROOT / "deploy/mac-mini/.env.test"),
            "config", "--format", "json",
        ], cwd=ROOT, check=True, capture_output=True, text=True)
        return json.loads(result.stdout)

    def test_effective_compose_is_isolated_and_replaces_base_mounts(self):
        config = self.rendered_config()
        validate_compose(config, ROOT)
        self.assertEqual(PROJECT, config["name"])
        service = config["services"]["media-router"]
        self.assertEqual(CONTAINER, service["container_name"])
        self.assertEqual("127.0.0.1", service["ports"][0]["host_ip"])
        self.assertEqual("18088", service["ports"][0]["published"])
        text = json.dumps(config).lower()
        for forbidden in (
            "embyserver", "/opt/mediarouter", "/users/shared/iptvboss",
            "/users/shared/mediarouter", "0.0.0.0:18088",
        ):
            self.assertNotIn(forbidden, text)
        targets = {row["target"]: row for row in service["volumes"]}
        self.assertEqual(
            {"/data", "/fixtures", "/outputs/movies", "/outputs/series",
             "/outputs/live", "/iptvboss/outputs"},
            set(targets),
        )
        self.assertTrue(targets["/fixtures"]["read_only"])
        self.assertTrue(targets["/iptvboss/outputs"]["read_only"])
        self.assertEqual(
            "false",
            service["environment"]["MEDIA_ROUTER_SOURCE_ENTRY_SHADOW_LEDGER_ENABLED"],
        )

    def test_project_name_and_unsafe_configuration_are_rejected(self):
        config = self.rendered_config()
        config["name"] = "mediarouter"
        with self.assertRaisesRegex(HarnessError, "unsafe Compose project"):
            validate_compose(config, ROOT)
        config = self.rendered_config()
        config["services"]["media-router"]["environment"][
            "MEDIA_ROUTER_SOURCE_ENTRY_SHADOW_LEDGER_ENABLED"
        ] = "true"
        with self.assertRaisesRegex(HarnessError, "shadow ledger"):
            validate_compose(config, ROOT)

    def test_shell_scripts_have_portable_syntax(self):
        for script in ("mac-mini-test",):
            result = subprocess.run(["sh", "-n", str(SCRIPTS / script)],
                                    capture_output=True, text=True)
            self.assertEqual(0, result.returncode, result.stderr)

    def test_confirmation_guards_run_before_destructive_commands(self):
        for command in ("reset", "destroy"):
            result = subprocess.run([str(SCRIPTS / "mac-mini-test"), command],
                                    cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(2, result.returncode)
            self.assertIn("--confirm mac-mini-test", result.stderr)
        result = subprocess.run(
            [str(SCRIPTS / "mac-mini-test"), "restore", "missing"],
            cwd=ROOT, capture_output=True, text=True,
        )
        self.assertEqual(2, result.returncode)

    def test_safe_deletion_boundaries_reject_other_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            unrelated = Path(temp)
            with self.assertRaisesRegex(HarnessError, "unexpected directory"):
                safe_reset(unrelated, ROOT)
            with self.assertRaisesRegex(HarnessError, "unexpected directory"):
                safe_destroy(unrelated, ROOT)

    def test_feed_uuid_is_created_restrictively_and_persists(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "feed-id"
            first = ensure_feed_id(path)
            second = ensure_feed_id(path)
            self.assertEqual(first, second)
            self.assertEqual(0o600, stat.S_IMODE(path.stat().st_mode))

    def test_secret_permissions(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "secrets.env"
            path.write_text("MAC_TEST_EMBY_API_KEY=[test-only]\n")
            os.chmod(path, 0o644)
            with self.assertRaisesRegex(HarnessError, "0600"):
                validate_secret_permissions(path)
            os.chmod(path, 0o600)
            validate_secret_permissions(path)

    def test_emby_target_allowlist_and_denylist(self):
        allowed = "host.docker.internal:8597,localhost:8597"
        self.assertEqual(
            "http://host.docker.internal:8597",
            validate_emby_target(
                "http://host.docker.internal:8597", "mac-mini-test", allowed
            ),
        )
        self.assertEqual(
            "http://localhost:8597",
            validate_emby_target("http://localhost:8597", "mac-mini-test", allowed),
        )
        rejected = (
            "http://embyserver:8096",
            "https://example.com",
            "http://host.docker.internal:8096",
            "http://user:secret@host.docker.internal:8597",
            "",
        )
        for url in rejected:
            with self.subTest(url=url), self.assertRaises(HarnessError):
                validate_emby_target(url, "mac-mini-test", allowed)
        with self.assertRaisesRegex(HarnessError, "environment"):
            validate_emby_target(
                "http://host.docker.internal:8597", "production", allowed
            )

    def test_fixture_manifest_and_structural_counts(self):
        manifest_path = ROOT / "tests/fixtures/mac-mini/expected/fixture-manifest.json"
        manifest = json.loads(manifest_path.read_text())
        fixture = ROOT / "tests/fixtures/mac-mini" / manifest["fixture"]
        digest = hashlib.sha256(fixture.read_bytes()).hexdigest()
        self.assertEqual(manifest["sha256"], digest)
        lines = fixture.read_text().splitlines()
        self.assertEqual(manifest["entry_count"], sum(line.startswith("#EXTINF") for line in lines))
        fixture_text = fixture.read_text().lower()
        self.assertNotRegex(fixture_text, r"https?://[^/\s]+:[^@\s]+@")
        self.assertNotIn("token=", fixture_text)
        self.assertNotIn("password", fixture_text)
        self.assertTrue(all(
            line.startswith("http://media.invalid/")
            for line in lines if line.startswith("http://")
        ))
        observed = list(parse_m3u_entries(str(fixture)))
        counts = {
            "movie": sum(row.media_type == "movie" for row in observed),
            "episode": sum(row.media_type == "episode" for row in observed),
        }
        self.assertEqual({"movie": 5, "episode": 4}, counts)

    def test_archive_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / "unsafe.tar.gz"
            payload = Path(temp) / "payload"
            payload.write_text("unsafe")
            with tarfile.open(archive, "w:gz") as bundle:
                bundle.add(payload, arcname="../escape")
            with self.assertRaisesRegex(HarnessError, "unsafe path"):
                validate_archive(archive)
            with self.assertRaises(HarnessError):
                restore_archive(archive, Path(temp) / "local")

    def test_snapshot_manifest_excludes_secret_contents_and_no_deployment_runs(self):
        config = self.rendered_config()
        with tempfile.TemporaryDirectory() as temp:
            fake_bin = Path(temp)
            log = fake_bin / "docker.log"
            rendered = fake_bin / "config.json"
            rendered.write_text(json.dumps(config))
            fake = fake_bin / "docker"
            fake.write_text(
                "#!/bin/sh\n"
                f"printf '%s\\n' \"$*\" >>'{log}'\n"
                f"case \" $* \" in *' config --format json '*) cat '{rendered}';;"
                " *' images -q '*) echo sha256:test-image;;"
                " *' ps --status running -q '*) :;; esac\n"
            )
            fake.chmod(0o755)
            local = ROOT / ".local/mac-mini"
            secret = local / "secrets.env"
            secret.write_text("MAC_TEST_EMBY_API_KEY=never-print-this\n")
            secret.chmod(0o600)
            name = "unit-snapshot"
            shutil.rmtree(local / "snapshots" / name, ignore_errors=True)
            env = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"}
            result = subprocess.run(
                [str(SCRIPTS / "mac-mini-test"), "snapshot", name],
                cwd=ROOT, env=env, capture_output=True, text=True,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            manifest = json.loads((local / "snapshots" / name / "manifest.json").read_text())
            self.assertTrue(manifest["secrets_file_existed"])
            self.assertNotIn("never-print-this", json.dumps(manifest))
            archive = local / "snapshots" / name / "state.tar.gz"
            with tarfile.open(archive, "r:gz") as bundle:
                self.assertFalse(any("secrets" in member.name for member in bundle.getmembers()))
            commands = log.read_text()
            self.assertIn("config --format json", commands)
            self.assertNotIn(" up ", f" {commands} ")
            self.assertNotIn(" start ", f" {commands} ")
            shutil.rmtree(local / "snapshots" / name)
            secret.unlink()

    def test_smoke_runner_is_read_only_and_rejects_mutation_flags(self):
        source = (SCRIPTS / "mac-mini-smoke").read_text()
        for endpoint in (
            "/api/health", "/api/system", "/api/catalog/summary",
            "/api/integrations/emby/status", "/api/broker/status",
            "/api/broker/reservations", "/api/outputs/strm/settings",
            "/api/outputs/live-m3u/settings",
        ):
            self.assertIn(endpoint, source)
        self.assertNotIn("/api/catalog/import", source)
        self.assertNotIn("/api/outputs/strm/generate", source)
        for flag in ("--allow-import", "--allow-output-generation", "--allow-playback"):
            result = subprocess.run(
                [str(SCRIPTS / "mac-mini-smoke"), flag],
                cwd=ROOT, capture_output=True, text=True,
            )
            self.assertEqual(2, result.returncode)
            self.assertIn("not implemented", result.stderr)

    def test_lifecycle_script_pins_project_and_compose_files(self):
        source = (SCRIPTS / "mac-mini-test").read_text()
        self.assertIn("PROJECT=mediarouter-mac-test", source)
        self.assertIn("-p $PROJECT", source)
        self.assertIn("-f $REPO_ROOT/docker-compose.yml", source)
        self.assertIn("-f $REPO_ROOT/deploy/mac-mini/compose.test.yml", source)
        self.assertIn("--env-file $ENV_FILE", source)
        self.assertNotIn("ssh ", source)


if __name__ == "__main__":
    unittest.main()
