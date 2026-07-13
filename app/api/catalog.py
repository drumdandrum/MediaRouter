from fastapi import APIRouter, BackgroundTasks, HTTPException, Query

from app.schemas.catalog import CatalogImportAccepted, CatalogImportRequest, CatalogItem, CatalogSummary, ChannelPlacement, SourceAvailability, SourceAvailabilityUpdate
from app.services.catalog import (
    clear_test_data,
    delete_source_availability,
    get_summary,
    list_all_items,
    list_items,
    list_channel_placements,
    list_source_availability,
    normalize_playlist_sources,
    run_catalog_import_job,
    update_source_availability,
    validate_media_type_hint,
    validate_playlist_sources,
)
from app.services.jobs import create_job
from app.services.providers import get_account, get_provider

router = APIRouter(prefix="/api/catalog", tags=["catalog"])


@router.get("/summary", response_model=CatalogSummary)
def summary() -> CatalogSummary:
    return get_summary()


@router.get("/items", response_model=list[CatalogItem])
def catalog_items(limit: int = Query(200, ge=1, le=500), offset: int = Query(0, ge=0)) -> list[CatalogItem]:
    return list_all_items(limit, offset)


@router.get("/live", response_model=list[CatalogItem])
def live_channels(limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)) -> list[CatalogItem]:
    return list_items("channel", limit, offset)


@router.get("/movies", response_model=list[CatalogItem])
def movies(limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)) -> list[CatalogItem]:
    return list_items("movie", limit, offset)


@router.get("/series", response_model=list[CatalogItem])
def series(limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)) -> list[CatalogItem]:
    return list_items("series", limit, offset)


@router.get("/episodes", response_model=list[CatalogItem])
def episodes(limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)) -> list[CatalogItem]:
    return list_items("episode", limit, offset)


@router.get("/sources", response_model=list[SourceAvailability])
def sources(limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)) -> list[SourceAvailability]:
    return list_source_availability(limit=limit, offset=offset)


@router.get("/{catalog_internal_id}/sources", response_model=list[SourceAvailability])
def item_sources(catalog_internal_id: str, limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)) -> list[SourceAvailability]:
    return list_source_availability(catalog_internal_id, limit, offset)


@router.get("/{catalog_internal_id}/placements", response_model=list[ChannelPlacement])
def item_placements(catalog_internal_id: str, limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)) -> list[ChannelPlacement]:
    return list_channel_placements(catalog_internal_id, limit, offset)


@router.post("/import", response_model=CatalogImportAccepted, status_code=202)
def import_catalog(payload: CatalogImportRequest, background_tasks: BackgroundTasks) -> CatalogImportAccepted:
    paths = normalize_playlist_sources(payload.paths, payload.playlist)
    try:
        validate_media_type_hint(payload.media_type)
        validate_playlist_sources(paths)
        if not payload.provider_id:
            raise ValueError("Choose a provider before importing.")
        if not payload.account_id:
            raise ValueError("Choose an account/connection before importing.")
        provider = get_provider(payload.provider_id)
        if provider is None:
            raise ValueError("Selected provider was not found.")
        account = get_account(payload.account_id)
        if account is None:
            raise ValueError("Selected account/connection was not found.")
        if account.provider_id != provider.id:
            raise ValueError("Selected account does not belong to the selected provider.")
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    job = create_job("catalog_import")
    background_tasks.add_task(run_catalog_import_job, job.id, paths, payload.source_name, payload.provider_id, payload.account_id, payload.media_type)
    return CatalogImportAccepted(job_id=job.id, status=job.status, message="Catalog import queued")


@router.post("/clear-test-data", response_model=CatalogSummary)
def clear_catalog_test_data() -> CatalogSummary:
    return clear_test_data()
