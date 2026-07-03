from dataclasses import asdict, dataclass
from datetime import datetime
import re
from uuid import uuid4

from app.schemas.logs import LogEntry


SENSITIVE_MARKERS = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
)


@dataclass
class LogRecord:
    id: str
    level: str
    category: str
    message: str
    created_at: datetime


LOGS: list[LogRecord] = []


def scrub(message: str) -> str:
    scrubbed = message
    for marker in SENSITIVE_MARKERS:
        scrubbed = re.sub(
            rf"({marker})(\s*[:=]\s*)([^\s,;]+)",
            rf"\1\2[redacted]",
            scrubbed,
            flags=re.IGNORECASE,
        )
        scrubbed = scrubbed.replace(marker, "[redacted]")
        scrubbed = scrubbed.replace(marker.upper(), "[redacted]")
    return scrubbed


def add_log(level: str, category: str, message: str) -> LogEntry:
    record = LogRecord(
        id=uuid4().hex,
        level=level,
        category=category,
        message=scrub(message),
        created_at=datetime.utcnow(),
    )
    LOGS.insert(0, record)
    del LOGS[100:]
    return LogEntry(**asdict(record))


def list_logs() -> list[LogEntry]:
    return [LogEntry(**asdict(record)) for record in LOGS]


def log_count() -> int:
    return len(LOGS)
