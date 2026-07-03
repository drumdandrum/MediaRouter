import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime
from uuid import uuid4

from app.schemas.jobs import JobRead


@dataclass
class Job:
    id: str
    kind: str
    status: str
    progress: int
    message: str
    created_at: datetime
    updated_at: datetime


JOBS: dict[str, Job] = {}


def _read(job: Job) -> JobRead:
    return JobRead(**asdict(job))


def list_jobs() -> list[JobRead]:
    return [_read(job) for job in sorted(JOBS.values(), key=lambda item: item.created_at, reverse=True)]


def get_job(job_id: str) -> JobRead | None:
    job = JOBS.get(job_id)
    return _read(job) if job else None


def create_job(kind: str) -> JobRead:
    now = datetime.utcnow()
    job = Job(
        id=uuid4().hex,
        kind=kind,
        status="queued",
        progress=0,
        message="Queued",
        created_at=now,
        updated_at=now,
    )
    JOBS[job.id] = job
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
    job.updated_at = datetime.utcnow()
    for progress, message in checkpoints:
        await asyncio.sleep(0.3)
        job.progress = progress
        job.message = message
        job.updated_at = datetime.utcnow()
    job.status = "complete"
    job.updated_at = datetime.utcnow()


def job_counts() -> tuple[int, int]:
    total = len(JOBS)
    running = sum(1 for job in JOBS.values() if job.status in {"queued", "running"})
    return total, running
