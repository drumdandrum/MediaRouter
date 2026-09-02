import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from starlette.requests import Request

from app.api.foundation import health
from app.main import app, index
from app.main_meta import APP_VERSION
from app.services.system import get_system_info


class VersionMetadataTests(unittest.TestCase):
    def test_packaging_defaults_match_source_and_do_not_claim_legacy_metadata(self):
        root = Path(__file__).resolve().parents[1]
        dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
        compose = (root / "docker-compose.yml").read_text(encoding="utf-8")

        self.assertIn("ARG MEDIA_ROUTER_APP_VERSION=v0.10.0-rc.4", dockerfile)
        self.assertEqual(2, compose.count('MEDIA_ROUTER_APP_VERSION: "v0.10.0-rc.4"'))
        self.assertEqual(2, compose.count("${MEDIA_ROUTER_GIT_COMMIT:-Unavailable}"))
        self.assertNotIn("v0.8.1", dockerfile)
        self.assertNotIn("v0.8.1", compose)
        self.assertNotIn("fdc842f", compose)

    def test_source_openapi_and_template_use_release_default(self):
        self.assertEqual("v0.10.0-rc.4", APP_VERSION)
        self.assertNotEqual("v0.8.1", APP_VERSION)
        self.assertEqual(APP_VERSION, app.version)
        self.assertEqual(APP_VERSION, app.openapi()["info"]["version"])

        request = Request(
            {
                "type": "http",
                "http_version": "1.1",
                "method": "GET",
                "scheme": "http",
                "path": "/",
                "raw_path": b"/",
                "query_string": b"",
                "headers": [],
                "client": ("127.0.0.1", 1),
                "server": ("testserver", 80),
                "root_path": "",
            }
        )
        self.assertEqual(APP_VERSION, index(request).context["app_version"])

    def test_explicit_deployment_version_overrides_default_and_preserves_git_metadata(self):
        runtime = SimpleNamespace(data_dir=Path("/data"), environment_mode="development")
        persisted = SimpleNamespace(app_name="Media Router")
        environment = {
            "MEDIA_ROUTER_APP_VERSION": "v0.10.0-rc.99",
            "MEDIA_ROUTER_GIT_BRANCH": "release/metadata-test",
            "MEDIA_ROUTER_GIT_COMMIT": "0123456789abcdef",
        }
        with (
            patch.dict(os.environ, environment, clear=False),
            patch("app.services.system.get_settings", return_value=runtime),
            patch("app.services.system.get_app_settings", return_value=persisted),
            patch("app.services.system.os.path.exists", return_value=True),
        ):
            info = get_system_info()

        self.assertEqual("v0.10.0-rc.99", info.app_version)
        self.assertEqual("release/metadata-test", info.git_branch)
        self.assertEqual("0123456789abcdef", info.git_commit)

    def test_missing_deployment_version_falls_back_to_current_source_default(self):
        runtime = SimpleNamespace(data_dir=Path("/data"), environment_mode="development")
        persisted = SimpleNamespace(app_name="Media Router")
        with (
            patch.dict(
                os.environ,
                {
                    "MEDIA_ROUTER_APP_VERSION": "",
                    "MEDIA_ROUTER_GIT_BRANCH": "",
                    "MEDIA_ROUTER_GIT_COMMIT": "",
                },
                clear=False,
            ),
            patch("app.services.system.get_settings", return_value=runtime),
            patch("app.services.system.get_app_settings", return_value=persisted),
            patch("app.services.system.os.path.exists", return_value=True),
            patch("app.services.system._run", return_value="Unavailable"),
        ):
            info = get_system_info()

        self.assertEqual(APP_VERSION, info.app_version)
        self.assertNotEqual("v0.8.1", info.app_version)
        self.assertEqual("Unavailable", info.git_branch)
        self.assertEqual("Unavailable", info.git_commit)

    def test_health_contract_is_unchanged(self):
        self.assertEqual({"status": "ready"}, health())


if __name__ == "__main__":
    unittest.main()
