from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from app.schemas.jobs import JobRead
from app.schemas.outputs import (
    GeneratedOutputFile,
    LiveM3uOutputResult,
    LiveM3uGenerateRequest,
    LiveM3uEstimate,
    LiveM3uRunHistory,
    LiveM3uSettings,
    LiveM3uSettingsUpdate,
    OutputPathValidationResult,
    OutputRunHistory,
    StrmOutputResult,
    StrmGenerateRequest,
    StrmSettings,
    StrmSettingsUpdate,
)
from app.services.jobs import create_job
from app.services.outputs import (
    dry_run_strm_outputs,
    dry_run_live_m3u_output,
    get_strm_settings,
    get_live_m3u_settings,
    get_live_m3u_estimate,
    list_generated_files,
    list_live_m3u_history,
    list_output_history,
    preview_live_m3u_output,
    run_live_m3u_generate_job,
    run_strm_generate_job,
    update_live_m3u_settings,
    update_strm_settings,
    validate_live_m3u_paths,
    validate_strm_paths,
)

router = APIRouter(prefix="/api/outputs", tags=["outputs"])


def _request_base_url(request: Request) -> str:
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.headers.get("host", request.url.netloc))
    return f"{scheme}://{host}"


@router.get("/strm/settings", response_model=StrmSettings)
def read_strm_settings() -> StrmSettings:
    return get_strm_settings()


@router.put("/strm/settings", response_model=StrmSettings)
def write_strm_settings(payload: StrmSettingsUpdate) -> StrmSettings:
    try:
        return update_strm_settings(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/strm/dry-run", response_model=StrmOutputResult)
def strm_dry_run(request: Request) -> StrmOutputResult:
    try:
        return dry_run_strm_outputs(_request_base_url(request))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/strm/validate-paths", response_model=OutputPathValidationResult)
def strm_validate_paths() -> OutputPathValidationResult:
    try:
        return validate_strm_paths(create_missing=True)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/strm/generate", response_model=JobRead, status_code=201)
def strm_generate(request: Request, background_tasks: BackgroundTasks, payload: StrmGenerateRequest = StrmGenerateRequest()) -> JobRead:
    settings = get_strm_settings()
    if settings.generation_mode == "Unlimited" and not payload.confirm_unlimited:
        raise HTTPException(status_code=400, detail="Unlimited STRM generation requires explicit confirmation.")
    job = create_job("strm_generate")
    background_tasks.add_task(run_strm_generate_job, job.id, _request_base_url(request))
    return job


@router.get("/strm/history", response_model=list[OutputRunHistory])
def strm_history() -> list[OutputRunHistory]:
    return list_output_history()


@router.get("/strm/generated-files", response_model=list[GeneratedOutputFile])
def strm_generated_files(limit: int = 100, offset: int = 0) -> list[GeneratedOutputFile]:
    return list_generated_files(limit=limit, offset=offset)


@router.get("/live-m3u/settings", response_model=LiveM3uSettings)
def read_live_m3u_settings() -> LiveM3uSettings:
    return get_live_m3u_settings()


@router.get("/live-m3u/estimate", response_model=LiveM3uEstimate)
def live_m3u_estimate() -> LiveM3uEstimate:
    return get_live_m3u_estimate()


@router.put("/live-m3u/settings", response_model=LiveM3uSettings)
def write_live_m3u_settings(payload: LiveM3uSettingsUpdate) -> LiveM3uSettings:
    try:
        return update_live_m3u_settings(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/live-m3u/validate-paths", response_model=OutputPathValidationResult)
def live_m3u_validate_paths() -> OutputPathValidationResult:
    try:
        return validate_live_m3u_paths(create_missing=True)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/live-m3u/dry-run", response_model=LiveM3uOutputResult)
def live_m3u_dry_run(request: Request) -> LiveM3uOutputResult:
    try:
        return dry_run_live_m3u_output(_request_base_url(request))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/live-m3u/generate", response_model=JobRead, status_code=201)
def live_m3u_generate(request: Request, background_tasks: BackgroundTasks, payload: LiveM3uGenerateRequest = LiveM3uGenerateRequest()) -> JobRead:
    settings = get_live_m3u_settings()
    if settings.generation_mode == "Unlimited" and not payload.confirm_unlimited:
        raise HTTPException(status_code=400, detail="Unlimited Live M3U generation requires explicit confirmation.")
    job = create_job("live_m3u_generate")
    background_tasks.add_task(run_live_m3u_generate_job, job.id, _request_base_url(request))
    return job


@router.get("/live-m3u/history", response_model=list[LiveM3uRunHistory])
def live_m3u_history() -> list[LiveM3uRunHistory]:
    return list_live_m3u_history()


@router.get("/live-m3u/preview", response_model=LiveM3uOutputResult)
def live_m3u_preview(request: Request) -> LiveM3uOutputResult:
    try:
        return preview_live_m3u_output(_request_base_url(request))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
