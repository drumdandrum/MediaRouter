import hashlib
import io
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
from unittest import mock

from app.services.catalog import parse_m3u_entries


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
from mac_mini_harness import (  # noqa: E402
    CONTAINER,
    HarnessError,
    PROJECT,
    credential_file_state,
    create_archive,
    ensure_feed_id,
    load_test_emby_api_key,
    restore_archive,
    safe_destroy,
    safe_reset,
    validate_archive,
    validate_compose,
    validate_emby_target,
    validate_local_root,
    validate_secret_permissions,
)


class MacMiniHarnessTests(unittest.TestCase):
    def rendered_config(self, repo=ROOT):
        result = subprocess.run([
            "docker", "compose", "-p", PROJECT,
            "-f", str(repo / "docker-compose.yml"),
            "-f", str(repo / "deploy/mac-mini/compose.test.yml"),
            "--env-file", str(repo / "deploy/mac-mini/env.test.example"),
            "config", "--format", "json",
        ], cwd=repo, check=True, capture_output=True, text=True)
        return json.loads(result.stdout)

    def temporary_harness_repo(self, parent: Path) -> Path:
        repo = parent / "repo"
        (repo / "scripts").mkdir(parents=True)
        (repo / "deploy/mac-mini").mkdir(parents=True)
        (repo / "tests/fixtures/mac-mini/catalog").mkdir(parents=True)
        for relative in (
            "scripts/mac-mini-test",
            "scripts/mac-mini-smoke",
            "scripts/mac_mini_harness.py",
            "docker-compose.yml",
            "deploy/mac-mini/compose.test.yml",
            "deploy/mac-mini/env.test.example",
            "tests/fixtures/mac-mini/catalog/vod-small.m3u",
        ):
            destination = repo / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, destination)
        subprocess.run(
            [str(repo / "scripts/mac-mini-test"), "init"],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        )
        return repo

    def test_effective_compose_is_isolated_and_replaces_base_mounts(self):
        config = self.rendered_config()
        validate_compose(config, ROOT)
        self.assertNotIn("MEDIA_ROUTER_TEST_EMBY_API_KEY", json.dumps(config))
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
        config = self.rendered_config()
        config["services"]["unexpected"] = {"image": "busybox"}
        with self.assertRaisesRegex(HarnessError, "only the media-router"):
            validate_compose(config, ROOT)

    def test_shell_scripts_have_portable_syntax(self):
        for script in ("mac-mini-test", "mac-mini-smoke"):
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
            with self.assertRaisesRegex(HarnessError, "unexpected local test root"):
                safe_reset(unrelated, ROOT)
            with self.assertRaisesRegex(HarnessError, "unexpected local test root"):
                safe_destroy(unrelated, ROOT)

    def test_reset_and_destroy_credential_state_semantics(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repo"
            local = repo / ".local/mac-mini"
            (local / "data").mkdir(parents=True)
            for name in ("outputs", "logs", "evidence", "snapshots"):
                (local / name).mkdir()
            settings = local / "data/emby_integration_settings.json"
            settings.write_text("synthetic-local-settings")
            (local / "feed-id").write_text("synthetic-feed")
            (local / "secrets.env").write_text("synthetic-secret")
            safe_reset(local, repo)
            self.assertFalse(settings.exists())
            self.assertEqual("synthetic-feed", (local / "feed-id").read_text())
            self.assertEqual("synthetic-secret", (local / "secrets.env").read_text())
            safe_destroy(local, repo)
            self.assertFalse(local.exists())

    def test_symlinked_local_root_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repo"
            outside = Path(temp) / "outside"
            repo.mkdir()
            outside.mkdir()
            (repo / ".local").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(HarnessError, "symlinks"):
                validate_local_root(repo / ".local/mac-mini", repo)
            self.assertTrue(outside.exists())
            (repo / ".local").unlink()
            local = repo / ".local/mac-mini"
            local.mkdir(parents=True)
            (local / "outputs").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(HarnessError, "managed"):
                validate_local_root(local, repo)

    def test_feed_uuid_is_created_restrictively_and_persists(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "feed-id"
            first = ensure_feed_id(path)
            second = ensure_feed_id(path)
            self.assertEqual(first, second)
            self.assertEqual(0o600, stat.S_IMODE(path.stat().st_mode))

    def test_malformed_feed_uuid_fails_without_replacement(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "feed-id"
            path.write_text("not-a-uuid\n")
            with self.assertRaises(ValueError):
                ensure_feed_id(path)
            self.assertEqual("not-a-uuid\n", path.read_text())

    def test_feed_id_and_secrets_may_not_be_symlinks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "target"
            target.write_text("outside\n")
            feed = root / "feed-id"
            feed.symlink_to(target)
            with self.assertRaisesRegex(HarnessError, "symlink"):
                ensure_feed_id(feed)
            secret = root / "secrets.env"
            secret.symlink_to(target)
            os.chmod(target, 0o600)
            with self.assertRaisesRegex(HarnessError, "invalid_format"):
                validate_secret_permissions(secret)
            self.assertEqual("outside\n", target.read_text())

    def test_secret_permissions(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "secrets.env"
            path.write_text("MEDIA_ROUTER_TEST_EMBY_API_KEY=test-only\n")
            for mode in (0o644, 0o660, 0o604):
                with self.subTest(mode=oct(mode)):
                    os.chmod(path, mode)
                    self.assertEqual("unsafe_mode", credential_file_state(path))
                    with self.assertRaisesRegex(HarnessError, "unsafe_mode"):
                        validate_secret_permissions(path)
            os.chmod(path, 0o600)
            validate_secret_permissions(path)
            self.assertEqual("valid", credential_file_state(path))

    def test_credential_file_contract(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "secrets.env"
            self.assertEqual("missing_file", credential_file_state(path))

            cases = (
                ("# lab only\n\n", "missing_key"),
                ("MEDIA_ROUTER_TEST_EMBY_API_KEY=\n", "invalid_format"),
                (
                    "MEDIA_ROUTER_TEST_EMBY_API_KEY=first\n"
                    "MEDIA_ROUTER_TEST_EMBY_API_KEY=second\n",
                    "duplicate_key",
                ),
                ("OTHER_KEY=value\n", "unknown_variable"),
                ("MEDIA_ROUTER_TEST_EMBY_API_KEY=$(id)\n", "invalid_format"),
                ("MEDIA_ROUTER_TEST_EMBY_API_KEY=`id`\n", "invalid_format"),
                ("MEDIA_ROUTER_TEST_EMBY_API_KEY=value;id\n", "invalid_format"),
                ('MEDIA_ROUTER_TEST_EMBY_API_KEY="quoted"\n', "invalid_format"),
                (" MEDIA_ROUTER_TEST_EMBY_API_KEY=value\n", "invalid_format"),
                ("MEDIA_ROUTER_TEST_EMBY_API_KEY=value \n", "invalid_format"),
                ("MEDIA_ROUTER_TEST_EMBY_API_KEY=" + "a" * 1025 + "\n", "invalid_format"),
                ("MEDIA_ROUTER_TEST_EMBY_API_KEY=production\n", "invalid_format"),
                ("MEDIA_ROUTER_TEST_EMBY_API_KEY=embyserver\n", "invalid_format"),
            )
            for content, expected in cases:
                with self.subTest(expected=expected, content_length=len(content)):
                    path.write_text(content)
                    path.chmod(0o600)
                    self.assertEqual(expected, credential_file_state(path))

            path.write_text(
                "# dedicated Mac mini test credential\n"
                "\n"
                "MEDIA_ROUTER_TEST_EMBY_API_KEY=test_key-123.~\r\n"
            )
            path.chmod(0o600)
            self.assertEqual("valid", credential_file_state(path))
            self.assertEqual("test_key-123.~", load_test_emby_api_key(path))

    def test_credential_symlink_and_nul_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "target"
            target.write_text("MEDIA_ROUTER_TEST_EMBY_API_KEY=test-only\n")
            target.chmod(0o600)
            link = root / "secrets.env"
            link.symlink_to(target)
            self.assertEqual("invalid_format", credential_file_state(link))
            link.unlink()
            link.write_bytes(b"MEDIA_ROUTER_TEST_EMBY_API_KEY=test\x00key\n")
            link.chmod(0o600)
            self.assertEqual("invalid_format", credential_file_state(link))
            link.write_bytes(b"MEDIA_ROUTER_TEST_EMBY_API_KEY=\xff\n")
            self.assertEqual("invalid_format", credential_file_state(link))

    def test_credential_validation_is_sanitized_and_offline(self):
        secret = "do-not-print-this-key"
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repo"
            local = repo / ".local" / "mac-mini"
            local.mkdir(parents=True)
            path = local / "secrets.env"
            path.write_text(f"MEDIA_ROUTER_TEST_EMBY_API_KEY={secret};bad\n")
            path.chmod(0o600)
            with mock.patch("mac_mini_harness.urlopen") as network:
                state = credential_file_state(path)
                with self.assertRaises(HarnessError) as caught:
                    load_test_emby_api_key(path)
            network.assert_not_called()
            self.assertEqual("invalid_format", state)
            self.assertNotIn(secret, str(caught.exception))
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "mac_mini_harness.py"),
                    "credential-status",
                    str(path),
                    str(local),
                    str(repo),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(0, result.returncode)
            self.assertNotIn(secret, result.stdout + result.stderr)
            path.write_text("MEDIA_ROUTER_TEST_EMBY_API_KEY=test-only\n")
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "mac_mini_harness.py"),
                    "credential-status",
                    str(path),
                    str(local),
                    str(repo),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual("valid\n", result.stdout)

    def test_credential_status_rejects_symlinked_managed_root(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repo"
            local_parent = repo / ".local"
            outside = Path(temp) / "outside"
            local_parent.mkdir(parents=True)
            outside.mkdir()
            (outside / "secrets.env").write_text(
                "MEDIA_ROUTER_TEST_EMBY_API_KEY=test-only\n"
            )
            (outside / "secrets.env").chmod(0o600)
            (local_parent / "mac-mini").symlink_to(outside)
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "mac_mini_harness.py"),
                    "credential-status",
                    str(local_parent / "mac-mini" / "secrets.env"),
                    str(local_parent / "mac-mini"),
                    str(repo),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(0, result.returncode)
            self.assertNotIn("test-only", result.stdout + result.stderr)

    def test_credential_status_rejects_path_outside_managed_root(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repo"
            local = repo / ".local" / "mac-mini"
            local.mkdir(parents=True)
            outside = Path(temp) / "secrets.env"
            outside.write_text("MEDIA_ROUTER_TEST_EMBY_API_KEY=test-only\n")
            outside.chmod(0o600)
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "mac_mini_harness.py"),
                    "credential-status",
                    str(outside),
                    str(local),
                    str(repo),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(0, result.returncode)
            self.assertNotIn("test-only", result.stdout + result.stderr)

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
            "http://host.docker.internal:8597/",
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

    def test_archive_links_special_members_and_unexpected_roots_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            for kind in ("symlink", "hardlink", "fifo", "unexpected"):
                archive = temp_path / f"{kind}.tar.gz"
                with tarfile.open(archive, "w:gz") as bundle:
                    info = tarfile.TarInfo(
                        "data/link" if kind != "unexpected" else "secrets.env"
                    )
                    if kind in {"symlink", "hardlink"}:
                        info.type = (
                            tarfile.SYMTYPE if kind == "symlink" else tarfile.LNKTYPE
                        )
                        info.linkname = "data/target"
                    elif kind == "fifo":
                        info.type = tarfile.FIFOTYPE
                    else:
                        info.size = 0
                    bundle.addfile(info)
                with self.subTest(kind=kind), self.assertRaises(HarnessError):
                    validate_archive(archive)

    def test_archive_rejects_raw_emby_settings_member(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            payload = root / "payload"
            payload.write_text('{"api_key":"synthetic-never-restore"}')
            for index, member_name in enumerate((
                "data/emby_integration_settings.json",
                "data/EMBY_INTEGRATION_SETTINGS.JSON",
            )):
                with self.subTest(member_name=member_name):
                    archive = root / (member_name.rsplit("/", 1)[-1] + ".tar.gz")
                    with tarfile.open(archive, "w:gz") as bundle:
                        bundle.add(payload, arcname=member_name)
                    with self.assertRaisesRegex(HarnessError, "forbidden Emby"):
                        validate_archive(archive)
                    local = root / f"local-{index}"
                    (local / "data").mkdir(parents=True)
                    (local / "outputs").mkdir()
                    settings = local / "data/emby_integration_settings.json"
                    preserved = b'{"api_key":"synthetic-current-key"}'
                    settings.write_bytes(preserved)
                    settings.chmod(0o600)
                    with self.assertRaisesRegex(HarnessError, "forbidden Emby"):
                        restore_archive(archive, local)
                    self.assertEqual(preserved, settings.read_bytes())

    def test_archive_rejects_duplicate_normalized_members(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "duplicate.tar.gz"
            first = tarfile.TarInfo("data/state.json")
            first.size = 3
            second = tarfile.TarInfo("./data/STATE.JSON")
            second.size = 3
            with tarfile.open(archive, "w:gz") as bundle:
                bundle.addfile(first, io.BytesIO(b"one"))
                bundle.addfile(second, io.BytesIO(b"two"))
            with self.assertRaisesRegex(HarnessError, "duplicate paths"):
                validate_archive(archive)

    def test_restore_preserves_current_emby_settings_and_omits_missing_settings(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            snapshot_source = root / "snapshot-source"
            (snapshot_source / "data").mkdir(parents=True)
            (snapshot_source / "outputs").mkdir()
            (snapshot_source / "data/catalog.json").write_text("snapshot")
            (snapshot_source / "outputs/result.txt").write_text("output")
            archive = root / "snapshot.tar.gz"
            metadata = create_archive(snapshot_source, archive)
            self.assertEqual(
                {"present": False, "api_key_included": False}, metadata
            )

            local = root / "with-settings"
            (local / "data").mkdir(parents=True)
            (local / "outputs").mkdir()
            settings = local / "data/emby_integration_settings.json"
            original = b'{"api_key":"synthetic-current-key","enabled":false}'
            settings.write_bytes(original)
            settings.chmod(0o600)
            feed_id = local / "feed-id"
            feed_id.write_text("synthetic-feed-id")
            secrets_file = local / "secrets.env"
            secrets_file.write_text("synthetic-local-secret")
            (local / "data/old.txt").write_text("old")
            restore_archive(archive, local)
            self.assertEqual(original, settings.read_bytes())
            self.assertEqual(0o600, stat.S_IMODE(settings.stat().st_mode))
            self.assertEqual("synthetic-feed-id", feed_id.read_text())
            self.assertEqual("synthetic-local-secret", secrets_file.read_text())
            self.assertFalse((local / "data/old.txt").exists())
            self.assertEqual("snapshot", (local / "data/catalog.json").read_text())

            fresh = root / "without-settings"
            (fresh / "data").mkdir(parents=True)
            (fresh / "outputs").mkdir()
            restore_archive(archive, fresh)
            self.assertFalse((fresh / "data/emby_integration_settings.json").exists())

    def test_snapshot_rejects_symlink_and_malformed_emby_settings_safely(self):
        marker = "synthetic-secret-must-not-leak"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            (source / "data").mkdir(parents=True)
            (source / "outputs").mkdir()
            settings = source / "data/emby_integration_settings.json"
            target = root / "target"
            target.write_text(marker)
            settings.symlink_to(target)
            archive = root / "snapshot.tar.gz"
            with self.assertRaises(HarnessError) as symlink_error:
                create_archive(source, archive)
            self.assertNotIn(marker, str(symlink_error.exception))
            self.assertFalse(archive.exists())

            settings.unlink()
            settings.write_text('{"api_key":"' + marker + '", invalid')
            with self.assertRaisesRegex(HarnessError, "metadata is malformed") as malformed:
                create_archive(source, archive)
            self.assertNotIn(marker, str(malformed.exception))
            self.assertFalse(archive.exists())

            settings.write_text(json.dumps({
                "api_key": marker,
                "server_url": "http://[" + marker,
                "enabled": False,
            }))
            with self.assertRaisesRegex(HarnessError, "unsafe URL") as unsafe_url:
                create_archive(source, archive)
            self.assertNotIn(marker, str(unsafe_url.exception))
            self.assertFalse(archive.exists())

    def test_restore_rejects_symlinked_current_emby_settings(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            (source / "data").mkdir(parents=True)
            (source / "outputs").mkdir()
            archive = root / "snapshot.tar.gz"
            create_archive(source, archive)
            local = root / "local"
            (local / "data").mkdir(parents=True)
            (local / "outputs").mkdir()
            target = root / "target"
            target.write_text("synthetic-current-secret")
            (local / "data/emby_integration_settings.json").symlink_to(target)
            with self.assertRaisesRegex(HarnessError, "regular non-symlink") as caught:
                restore_archive(archive, local)
            self.assertNotIn("synthetic-current-secret", str(caught.exception))
            self.assertEqual("synthetic-current-secret", target.read_text())

    def test_snapshot_names_cannot_traverse_or_be_options(self):
        for name in ("../outside", "-option", ".", ".."):
            result = subprocess.run(
                [str(SCRIPTS / "mac-mini-test"), "snapshot", name],
                cwd=ROOT, capture_output=True, text=True,
            )
            self.assertEqual(2, result.returncode)
            self.assertIn("Snapshot name", result.stderr)

    def test_compose_render_failure_prevents_start(self):
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            repo = self.temporary_harness_repo(temp_path)
            fake_bin = temp_path / "bin"
            fake_bin.mkdir()
            log = fake_bin / "docker.log"
            fake = fake_bin / "docker"
            fake.write_text(
                "#!/bin/sh\n"
                f"printf '%s\\n' \"$*\" >>'{log}'\n"
                "case \" $* \" in *' config --format json '*) exit 42;; esac\n"
            )
            fake.chmod(0o755)
            env = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"}
            result = subprocess.run(
                [str(repo / "scripts/mac-mini-test"), "start"],
                cwd=repo, env=env, capture_output=True, text=True,
            )
            self.assertNotEqual(0, result.returncode)
            commands = log.read_text()
            self.assertIn("config --format json", commands)
            self.assertNotIn(" up ", f" {commands} ")

    def test_snapshot_manifest_excludes_secret_contents_and_no_deployment_runs(self):
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            repo = self.temporary_harness_repo(temp_path)
            config = self.rendered_config(repo)
            fake_bin = temp_path / "bin"
            fake_bin.mkdir()
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
            git = fake_bin / "git"
            git.write_text(
                "#!/bin/sh\n"
                "case \" $* \" in"
                " *' branch --show-current '*) echo test-branch;;"
                " *' rev-parse HEAD '*) echo 0123456789abcdef;;"
                " esac\n"
            )
            git.chmod(0o755)
            local = repo / ".local/mac-mini"
            secret = local / "secrets.env"
            secret.write_text("MEDIA_ROUTER_TEST_EMBY_API_KEY=never-print-this\n")
            secret.chmod(0o600)
            emby_secret = "synthetic-emby-key-never-archive"
            settings = local / "data/emby_integration_settings.json"
            settings.write_text(json.dumps({
                "enabled": True,
                "server_url": "http://host.docker.internal:8597",
                "api_key": emby_secret,
                "unknown": "omit-me",
            }))
            similarly_named = local / "data/emby_integration_settings.json.backup"
            similarly_named.write_text("safe-similarly-named-state")
            name = "unit-snapshot"
            env = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"}
            result = subprocess.run(
                [str(repo / "scripts/mac-mini-test"), "snapshot", name],
                cwd=repo, env=env, capture_output=True, text=True,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            manifest = json.loads((local / "snapshots" / name / "manifest.json").read_text())
            self.assertTrue(manifest["secrets_file_existed"])
            self.assertEqual({
                "present": True,
                "enabled": True,
                "server_url": "http://host.docker.internal:8597",
                "api_key_included": False,
            }, manifest["emby_settings"])
            self.assertNotIn("never-print-this", json.dumps(manifest))
            self.assertNotIn(emby_secret, json.dumps(manifest))
            self.assertNotIn(hashlib.sha256(emby_secret.encode()).hexdigest(), json.dumps(manifest))
            self.assertNotIn(
                "never-print-this",
                (local / "snapshots" / name / "compose.effective.json").read_text(),
            )
            archive = local / "snapshots" / name / "state.tar.gz"
            self.assertNotIn(emby_secret.encode(), archive.read_bytes())
            with tarfile.open(archive, "r:gz") as bundle:
                self.assertFalse(any("secrets" in member.name for member in bundle.getmembers()))
                self.assertNotIn(
                    "data/emby_integration_settings.json",
                    {member.name for member in bundle.getmembers()},
                )
                self.assertIn(
                    "data/emby_integration_settings.json.backup",
                    {member.name for member in bundle.getmembers()},
                )
                for member in bundle.getmembers():
                    if member.isfile():
                        self.assertNotIn(emby_secret.encode(), bundle.extractfile(member).read())
            commands = log.read_text()
            self.assertIn("config --format json", commands)
            self.assertNotIn(" up ", f" {commands} ")
            self.assertNotIn(" start ", f" {commands} ")
            self.assertNotIn(emby_secret, result.stdout + result.stderr + commands)

    def test_smoke_runner_is_read_only_and_rejects_mutation_flags(self):
        source = (
            (SCRIPTS / "mac-mini-smoke").read_text()
            + (SCRIPTS / "mac_mini_harness.py").read_text()
        )
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
        self.assertIn('snapshot "$pre_name"', source)
        self.assertNotIn("ssh ", source)


if __name__ == "__main__":
    unittest.main()
