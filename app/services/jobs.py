import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime
import json
from uuid import uuid4

from app.core.config import get_settings
from app.schemas.jobs import JobRead
from app.services.logs import add_log


@dataclass
class Job:
    id: str
    kind: str
    status: str
    progress: int
    message: str
    result: dict | None
    created_at: datetime
    updated_at: datetime


JOBS: dict[str, Job] = {}


def _jobs_path():
    return get_settings().data_dir / "jobs.json"


def _serialize(job: Job) -> dict:
    data = asdict(job)
    data["created_at"] = job.created_at.isoformat()
    data["updated_at"] = job.updated_at.isoformat()
    return data


def _persist() -> None:
    path = _jobs_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    jobs = [_serialize(job) for job in sorted(JOBS.values(), key=lambda item: item.created_at, reverse=True)]
    path.write_text(json.dumps(jobs, indent=2, default=str) + "\n")


def _load() -> None:
    path = _jobs_path()
    if not path.exists():
        return
    try:
        rows = json.loads(path.read_text())
    except json.JSONDecodeError:
        return
    for row in rows:
        status = row.get("status", "complete")
        if status in {"queued", "running"}:
            status = "interrupted"
        job = Job(
            id=row["id"],
            kind=row["kind"],
            status=status,
            progress=int(row.get("progress", 0)),
            message=row.get("message", "Restored from history"),
            result=row.get("result"),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
        JOBS[job.id] = job


_load()


def _read(job: Job) -> JobRead:
    return JobRead(**asdict(job))


def list_jobs() -> list[JobRead]:
    return [_read(job) for job in sorted(JOBS.values(), key=lambda item: item.created_at, reverse=True)]


def get_job(job_id: str) -> JobRead | None:
    job = JOBS.get(job_id)
    return _read(job) if job else None


def update_job(job_id: str, *, status: str | None = None, progress: int | None = None, message: str | None = None, result: dict | None = None) -> JobRead | None:
    job = JOBS.get(job_id)
    if job is None:
        return None
    if status is not None:
        job.status = status
    if progress is not None:
        job.progress = progress
    if message is not None:
        job.message = message
    if result is not None:
        job.result = result
    job.updated_at = datetime.utcnow()
    _persist()
    return _read(job)


def create_job(kind: str) -> JobRead:
    now = datetime.utcnow()
    job = Job(
        id=uuid4().hex,
        kind=kind,
        status="queued",
        progress=0,
        message="Queued",
        result=None,
        created_at=now,
        updated_at=now,
    )
    JOBS[job.id] = job
    _persist()
    add_log("info", "jobs", f"Queued job {kind}")
    return _read(job)


async def run_job(job_id: str) -> None:
    job = JOBS[job_id]
    checkpoints = [
        (10, "Checking foundation modules"),
        (35, "Checking settings store"),
        (60, "Checking wizard state"),
        (85, "Checking dashboard contract"),
        (100, "Complete"),
    ]
    job.status = "running"
    add_log("info", "jobs", f"Running job {job.kind}")
    job.updated_at = datetime.utcnow()
    _persist()
    for progress, message in checkpoints:
        await asyncio.sleep(0.3)
        job.progress = progress
        job.message = message
        job.updated_at = datetime.utcnow()
        _persist()
    job.status = "complete"
    job.updated_at = datetime.utcnow()
    _persist()
    add_log("info", "jobs", f"Completed job {job.kind}")


def job_counts() -> tuple[int, int]:
    total = len(JOBS)
    running = sum(1 for job in JOBS.values() if job.status in {"queued", "running"})
    return total, running
