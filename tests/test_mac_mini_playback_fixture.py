import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import mac_mini_playback_fixture as fixture  # noqa: E402


class _Response:
    def __init__(self, status, headers, body=b""):
        self.status = status
        self.headers = headers
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit=None):
        return self._body


class _Opener:
    def open(self, request, timeout):
        self.timeout = timeout
        headers = {
            "Content-Length": "4096",
            "Content-Type": "video/mp4",
            "ETag": '"fixture"',
            "Last-Modified": "Wed, 05 Aug 2026 00:00:00 GMT",
        }
        if request.get_method() == "HEAD":
            return _Response(200, headers)
        headers.update({"Content-Length": "1024", "Content-Range": "bytes 0-1023/4096"})
        return _Response(206, headers, b"x" * 1024)


class _RedirectingOpener:
    def open(self, _request, _timeout=None, **_kwargs):
        raise fixture.FixtureError("fixture probe refuses redirects")


class PlaybackFixtureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name) / "repo with spaces"
        (self.repo / ".local/mac-mini").mkdir(parents=True)
        (self.repo / "deploy/mac-mini").mkdir(parents=True)
        (self.repo / "deploy/mac-mini/playback-fixture.nginx.conf").write_text("synthetic")

    def tearDown(self):
        self.temp.cleanup()

    def compose_config(self):
        paths = fixture.fixture_paths(self.repo)
        return {
            "name": fixture.PROJECT,
            "services": {
                fixture.SERVICE: {
                    "container_name": fixture.CONTAINER,
                    "image": fixture.IMAGE,
                    "platform": "linux/arm64",
                    "restart": "no",
                    "read_only": True,
                    "user": "101:101",
                    "ports": [{"host_ip": "127.0.0.1", "published": "18091", "target": 8080}],
                    "volumes": [
                        {"source": str(paths["media"]), "target": "/srv/media", "read_only": True},
                        {"source": str(paths["nginx"]), "target": "/etc/nginx/nginx.conf", "read_only": True},
                    ],
                    "cap_drop": ["ALL"],
                    "security_opt": ["no-new-privileges:true"],
                    "tmpfs": ["/tmp:size=16m,mode=1777"],
                    "healthcheck": {"test": ["CMD", "wget"]},
                }
            },
        }

    def test_generation_command_is_deterministic_and_non_overwriting(self):
        command = fixture.generation_command(Path("out.mp4"))
        self.assertIn("testsrc2=size=1280x720:rate=30:duration=20", command)
        self.assertIn("sine=frequency=440:sample_rate=48000:duration=20", command)
        self.assertIn("libx264", command)
        self.assertIn("aac", command)
        self.assertIn("-n", command)
        self.assertNotIn("-y", command)
        self.assertEqual(command, fixture.generation_command(Path("out.mp4")))

    def test_root_and_media_symlinks_are_rejected(self):
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        root = self.repo / ".local/mac-mini/playback-fixture"
        root.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(fixture.FixtureError, "symlinks"):
            fixture.validate_fixture_root(self.repo, create=True)

    def test_generation_requires_ffmpeg_and_does_not_run_it_in_tests(self):
        with mock.patch.object(fixture.shutil, "which", return_value=None), \
             mock.patch.object(fixture.subprocess, "run") as run:
            with self.assertRaisesRegex(fixture.FixtureError, "ffmpeg"):
                fixture.generate_fixture(self.repo)
        run.assert_not_called()

    def test_generation_records_digest_codec_and_protected_state(self):
        ffprobe = {
            "format": {"duration": "20.000000", "format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 1280, "height": 720, "r_frame_rate": "30/1"},
                {"codec_type": "audio", "codec_name": "aac"},
            ],
        }

        def fake_run(command, **_kwargs):
            if command[0] == "ffmpeg":
                Path(command[-1]).write_bytes(b"synthetic-mp4")
                return subprocess.CompletedProcess(command, 0, "", "")
            return subprocess.CompletedProcess(command, 0, json.dumps(ffprobe), "")

        with mock.patch.object(fixture.shutil, "which", return_value="/test/tool"), \
             mock.patch.object(fixture.subprocess, "run", side_effect=fake_run):
            result = fixture.generate_fixture(self.repo)
        paths = fixture.fixture_paths(self.repo)
        self.assertEqual(hashlib.sha256(b"synthetic-mp4").hexdigest(), result["sha256"])
        self.assertEqual(0o600, stat.S_IMODE(paths["manifest"].stat().st_mode))
        self.assertEqual(0o644, stat.S_IMODE(paths["file"].stat().st_mode))
        with mock.patch.object(fixture.subprocess, "run") as run:
            with self.assertRaisesRegex(fixture.FixtureError, "already exists"):
                fixture.generate_fixture(self.repo)
        run.assert_not_called()

    def test_compose_is_exact_loopback_read_only_and_unprivileged(self):
        config = self.compose_config()
        fixture.validate_compose(config, self.repo)
        service = config["services"][fixture.SERVICE]
        self.assertEqual("127.0.0.1", service["ports"][0]["host_ip"])
        self.assertTrue(all(row["read_only"] for row in service["volumes"]))
        self.assertIn("@sha256:", service["image"])
        self.assertEqual(["ALL"], service["cap_drop"])
        self.assertEqual(["/tmp:size=16m,mode=1777"], service["tmpfs"])
        self.assertTrue(service["healthcheck"])

    def test_nginx_runtime_writes_are_confined_to_tmpfs(self):
        config = (ROOT / "deploy/mac-mini/playback-fixture.nginx.conf").read_text(encoding="utf-8")
        expected = {
            "pid": "/tmp/nginx.pid",
            "client_body_temp_path": "/tmp/nginx/client_temp",
            "proxy_temp_path": "/tmp/nginx/proxy_temp",
            "fastcgi_temp_path": "/tmp/nginx/fastcgi_temp",
            "uwsgi_temp_path": "/tmp/nginx/uwsgi_temp",
            "scgi_temp_path": "/tmp/nginx/scgi_temp",
        }
        for directive, path in expected.items():
            with self.subTest(directive=directive):
                self.assertIn(f"{directive} {path};", config)
        self.assertNotIn("/var/cache/nginx", config)
        self.assertIn("access_log /dev/stdout;", config)
        self.assertIn("error_log /dev/stderr warn;", config)

    def test_compose_rejects_additional_or_missing_writable_tmpfs(self):
        for tmpfs in ([], ["/tmp:size=16m,mode=1777", "/run"], ["/tmp:size=16m,mode=0755"]):
            with self.subTest(tmpfs=tmpfs):
                config = self.compose_config()
                config["services"][fixture.SERVICE]["tmpfs"] = tmpfs
                with self.assertRaisesRegex(fixture.FixtureError, "approved /tmp tmpfs"):
                    fixture.validate_compose(config, self.repo)

    def test_compose_rejects_wildcard_wrong_mount_image_and_privilege(self):
        mutations = [
            lambda c: c["services"][fixture.SERVICE]["ports"][0].update(host_ip="0.0.0.0"),
            lambda c: c["services"][fixture.SERVICE]["volumes"][0].update(read_only=False),
            lambda c: c["services"][fixture.SERVICE].update(image="nginx:latest"),
            lambda c: c["services"][fixture.SERVICE].update(privileged=True),
        ]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                config = self.compose_config()
                mutate(config)
                with self.assertRaises(fixture.FixtureError):
                    fixture.validate_compose(config, self.repo)

    def test_probe_requires_loopback_and_interprets_byte_ranges(self):
        with mock.patch.object(fixture, "build_opener", return_value=_Opener()):
            result = fixture.probe_fixture()
        self.assertEqual(206, result["range_status"])
        self.assertEqual(4096, result["content_length"])
        with self.assertRaisesRegex(fixture.FixtureError, "loopback"):
            fixture.probe_fixture("http://host.docker.internal:18091/playback-test.mp4")

    def test_probe_rejects_redirects(self):
        with mock.patch.object(fixture, "build_opener", return_value=_RedirectingOpener()):
            with self.assertRaisesRegex(fixture.FixtureError, "HTTP probe failed"):
                fixture.probe_fixture()

    def test_committed_playlist_is_one_lab_only_movie(self):
        manifest = fixture.validate_committed_fixture(ROOT)
        self.assertEqual(1, manifest["expected_catalog_behavior"]["entry_count"])
        self.assertEqual("movie", manifest["media_type"])
        original = ROOT / "tests/fixtures/mac-mini/catalog/vod-small.m3u"
        self.assertEqual(
            "9a9b2d32942aa25fd81ab3b1c7c4d4d598be13f7a33417516abfda3b53c31eb5",
            hashlib.sha256(original.read_bytes()).hexdigest(),
        )

    def test_committed_fixture_rejects_production_and_credentials(self):
        target = self.repo / "tests/fixtures/mac-mini/playback"
        target.mkdir(parents=True)
        source = ROOT / "tests/fixtures/mac-mini/playback"
        for name in ("playback-one.m3u", "fixture-manifest.json"):
            (target / name).write_bytes((source / name).read_bytes())
        playlist = target / "playback-one.m3u"
        playlist.write_text(playlist.read_text() + "# production token=secret\n")
        with self.assertRaisesRegex(fixture.FixtureError, "forbidden"):
            fixture.validate_committed_fixture(self.repo)

    def test_compose_rejects_forbidden_private_mount(self):
        config = self.compose_config()
        config["services"][fixture.SERVICE]["volumes"].append({
            "source": str(self.repo / ".local/mac-mini/data"),
            "target": "/data",
            "read_only": True,
        })
        with self.assertRaisesRegex(fixture.FixtureError, "mount"):
            fixture.validate_compose(config, self.repo)

    def test_lifecycle_commands_are_fixture_scoped_and_have_no_import_or_playback(self):
        script = (ROOT / "scripts/mac-mini-test").read_text(encoding="utf-8")
        fixture_section = script[script.index("  playback-fixture-generate)"):script.index("  destroy)")]
        self.assertIn("PLAYBACK_FIXTURE_SERVICE", fixture_section)
        self.assertNotIn('compose stop "$SERVICE"', fixture_section)
        self.assertNotIn('compose restart "$SERVICE"', fixture_section)
        for forbidden in ("/r/movie/", "/r/episode/", "catalog_import", "strm_generate", "embyserver"):
            self.assertNotIn(forbidden, fixture_section.casefold())

    def test_unit_tests_do_not_execute_real_docker_or_ffmpeg(self):
        with mock.patch.object(fixture.subprocess, "run") as run:
            fixture.generation_command(Path("fixture.mp4"))
            fixture.validate_compose(self.compose_config(), self.repo)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
