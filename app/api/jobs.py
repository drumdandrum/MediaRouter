from fastapi import APIRouter, BackgroundTasks, HTTPException

from app.schemas.jobs import JobCreate, JobRead
from app.services.jobs import create_job, get_job, list_jobs, run_job

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("", response_model=list[JobRead])
def read_jobs() -> list[JobRead]:
    return list_jobs()


@router.post("", response_model=JobRead, status_code=201)
def start_job(payload: JobCreate, background_tasks: BackgroundTasks) -> JobRead:
    job = create_job(payload.kind)
    background_tasks.add_task(run_job, job.id)
    return job


@router.get("/{job_id}", response_model=JobRead)
def read_job(job_id: str) -> JobRead:
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job
