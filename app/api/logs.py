from fastapi import APIRouter

from app.schemas.logs import LogCreate, LogEntry
from app.services.logs import add_log, list_logs

router = APIRouter(prefix="/api/logs", tags=["logs"])


@router.get("", response_model=list[LogEntry])
def read_logs() -> list[LogEntry]:
    return list_logs()


@router.post("", response_model=LogEntry, status_code=201)
def write_log(payload: LogCreate) -> LogEntry:
    return add_log(payload.level, payload.category, payload.message)
