from fastapi import APIRouter, BackgroundTasks

from app.schemas.catalog import CatalogImportAccepted, CatalogImportRequest, CatalogItem, CatalogSource, CatalogSummary
from app.services.catalog import clear_test_data, get_summary, list_items, list_sources, run_catalog_import_job
from app.services.jobs import create_job

router = APIRouter(prefix="/api/catalog", tags=["catalog"])


@router.get("/summary", response_model=CatalogSummary)
def summary() -> CatalogSummary:
    return get_summary()


@router.get("/live", response_model=list[CatalogItem])
def live_channels() -> list[CatalogItem]:
    return list_items("channel")


@router.get("/movies", response_model=list[CatalogItem])
def movies() -> list[CatalogItem]:
    return list_items("movie")


@router.get("/series", response_model=list[CatalogItem])
def series() -> list[CatalogItem]:
    return list_items("series")


@router.get("/episodes", response_model=list[CatalogItem])
def episodes() -> list[CatalogItem]:
    return list_items("episode")


@router.get("/sources", response_model=list[CatalogSource])
def sources() -> list[CatalogSource]:
    return list_sources()


@router.post("/import", response_model=CatalogImportAccepted, status_code=202)
def import_catalog(payload: CatalogImportRequest, background_tasks: BackgroundTasks) -> CatalogImportAccepted:
    job = create_job("catalog_import")
    background_tasks.add_task(run_catalog_import_job, job.id, payload.paths, payload.source_name)
    return CatalogImportAccepted(job_id=job.id, status=job.status, message="Catalog import queued")


@router.post("/clear-test-data", response_model=CatalogSummary)
def clear_catalog_test_data() -> CatalogSummary:
    return clear_test_data()
