from dataclasses import asdict, dataclass
from datetime import datetime
import logging
from uuid import uuid4

from app.core.redaction import redact_text
from app.schemas.logs import LogEntry


@dataclass
class LogRecord:
    id: str
    level: str
    category: str
    message: str
    created_at: datetime


LOGS: list[LogRecord] = []
UVICORN_LOGGER = logging.getLogger("uvicorn.error")


def scrub(message: str) -> str:
    return redact_text(message)


def add_log(level: str, category: str, message: str) -> LogEntry:
    scrubbed = scrub(message)
    scrubbed_category = redact_text(category)
    record = LogRecord(
        id=uuid4().hex,
        level=level,
        category=scrubbed_category,
        message=scrubbed,
        created_at=datetime.utcnow(),
    )
    LOGS.insert(0, record)
    del LOGS[100:]
    uvicorn_method = getattr(UVICORN_LOGGER, level if level in {"debug", "info", "warning", "error", "critical"} else "info")
    uvicorn_method("[%s] %s", scrubbed_category, scrubbed)
    return LogEntry(**asdict(record))


def list_logs() -> list[LogEntry]:
    return [LogEntry(**asdict(record)) for record in LOGS]


def log_count() -> int:
    return len(LOGS)
