import json
import logging
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.core.config import get_settings
from app.core.redaction import REDACTED, UvicornAccessRedactionFilter, redact_text, redact_value
from app.services.jobs import JOBS, create_job, get_job, update_job
from app.services.logs import LOGS, add_log, list_logs


class SecretRedactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp.name) / "data"
        os.environ["MEDIA_ROUTER_DATA_DIR"] = str(self.data_dir)
        get_settings.cache_clear()
        JOBS.clear()
        LOGS.clear()

    def tearDown(self):
        JOBS.clear()
        LOGS.clear()
        get_settings.cache_clear()
        os.environ.pop("MEDIA_ROUTER_DATA_DIR", None)
        self.temp.cleanup()

    def test_text_redaction_removes_credentials_but_preserves_operational_ids(self):
        raw = (
            "catalog=movie-42 reservation=res-7 binding=bind-9 emby_item=2011768 "
            "provider=provider-lab account=account-lab "
            "url=https://alice:door-key@example.test/private/stream.m3u8?token=query-key&item=42 "
            "Authorization: Bearer bearer-key Cookie=session-cookie "
            "payload={\"api_key\":\"json-key\",\"signature\":\"signed-key\"}\nnext"
        )

        scrubbed = redact_text(raw)

        for secret in ("alice", "door-key", "private", "stream.m3u8", "query-key", "bearer-key", "session-cookie", "json-key", "signed-key"):
            self.assertNotIn(secret, scrubbed)
        for useful in ("movie-42", "res-7", "bind-9", "2011768", "provider-lab", "account-lab", "item=42"):
            self.assertIn(useful, scrubbed)
        self.assertIn("https://example.test/[redacted]?token=[redacted]&item=42", scrubbed)
        self.assertIn("\\nnext", scrubbed)

    def test_structured_redaction_recurses_without_discarding_safe_context(self):
        raw = {
            "provider_id": "provider-lab",
            "api-key": "top-secret",
            "nested": [{"authorization": "Bearer abc123", "item_id": "movie-42"}],
            "failure": "GET https://user:pass@example.test/watch/42?ticket=letmein failed",
        }

        scrubbed = redact_value(raw)

        self.assertEqual(REDACTED, scrubbed["api-key"])
        self.assertEqual(REDACTED, scrubbed["nested"][0]["authorization"])
        self.assertEqual("movie-42", scrubbed["nested"][0]["item_id"])
        serialized = json.dumps(scrubbed)
        for secret in ("top-secret", "abc123", "user", "pass", "watch", "letmein"):
            self.assertNotIn(secret, serialized)

    def test_log_boundary_scrubs_memory_and_runtime_logger(self):
        with patch("app.services.logs.UVICORN_LOGGER") as logger:
            add_log(
                "error",
                "provider=provider-lab",
                "catalog=movie-42 failed https://user:pass@example.test/watch?token=token-value",
            )

        entry = list_logs()[0]
        self.assertIn("movie-42", entry.message)
        self.assertIn("provider-lab", entry.category)
        self.assertNotIn("token-value", entry.message)
        self.assertNotIn("user", entry.message)
        self.assertNotIn("pass", entry.message)
        emitted = " ".join(str(arg) for arg in logger.error.call_args.args)
        self.assertNotIn("token-value", emitted)
        self.assertNotIn("user", emitted)
        self.assertNotIn("pass", emitted)

    def test_job_boundary_scrubs_api_reads_and_persisted_history(self):
        job = create_job("strm_generate")
        update_job(
            job.id,
            status="failed",
            message="movie-42 failed: https://user:pass@example.test/watch?token=token-value",
            result={"catalog_item_id": "movie-42", "api_key": "api-secret"},
        )

        restored = get_job(job.id)
        self.assertEqual("movie-42", restored.result["catalog_item_id"])
        self.assertEqual(REDACTED, restored.result["api_key"])
        persisted = (self.data_dir / "jobs.json").read_text()
        for secret in ("user", "pass", "token-value", "api-secret"):
            self.assertNotIn(secret, persisted)
        self.assertIn("movie-42", persisted)

    def test_uvicorn_access_target_is_sanitized_before_formatting(self):
        record = logging.LogRecord(
            "uvicorn.access",
            logging.INFO,
            "",
            0,
            '%s - "%s %s HTTP/%s" %d',
            (
                "127.0.0.1:1234",
                "GET",
                "/api/catalog/movie-42?token=access-secret&item=2011768%0Aforged",
                "1.1",
                400,
            ),
            None,
        )

        self.assertTrue(UvicornAccessRedactionFilter().filter(record))

        rendered = record.getMessage()
        self.assertNotIn("access-secret", rendered)
        self.assertNotIn("\nforged", rendered)
        self.assertIn("movie-42", rendered)
        self.assertIn("item=2011768", rendered)
        self.assertIn("token=[redacted]", rendered)
        self.assertIn("\\nforged", rendered)


if __name__ == "__main__":
    unittest.main()
