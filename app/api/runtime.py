from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

from app.schemas.runtime import RuntimePreview, RuntimeResolveDebug
from app.services.runtime import RuntimeResolveUnavailable, preview_runtime, resolve_runtime

router = APIRouter(tags=["runtime"])


def _runtime_error(exc: RuntimeResolveUnavailable) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail.model_dump())


@router.get("/api/runtime/preview/{catalog_item_id}", response_model=RuntimePreview)
def runtime_preview(catalog_item_id: str, request: Request) -> RuntimePreview:
    try:
        scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
        host = request.headers.get("x-forwarded-host", request.headers.get("host", request.url.netloc))
        return preview_runtime(catalog_item_id, f"{scheme}://{host}")
    except RuntimeResolveUnavailable as exc:
        raise _runtime_error(exc) from exc


def _runtime_response(
    route_media_type: str,
    catalog_item_id: str,
    debug: bool,
    ttl: int | None,
    client_label: str | None,
) -> RuntimeResolveDebug | RedirectResponse:
    try:
        payload, raw_location_ref = resolve_runtime(route_media_type, catalog_item_id, ttl, client_label)
    except RuntimeResolveUnavailable as exc:
        raise _runtime_error(exc) from exc
    if debug:
        return payload
    return RedirectResponse(raw_location_ref, status_code=302)


@router.get("/r/live/{catalog_item_id}", response_model=RuntimeResolveDebug)
def resolve_live(
    catalog_item_id: str,
    debug: bool = False,
    ttl: int | None = Query(default=None, ge=1, le=86400),
    client_label: str | None = None,
) -> RuntimeResolveDebug | RedirectResponse:
    return _runtime_response("live", catalog_item_id, debug, ttl, client_label)


@router.get("/r/movie/{catalog_item_id}", response_model=RuntimeResolveDebug)
def resolve_movie(
    catalog_item_id: str,
    debug: bool = False,
    ttl: int | None = Query(default=None, ge=1, le=86400),
    client_label: str | None = None,
) -> RuntimeResolveDebug | RedirectResponse:
    return _runtime_response("movie", catalog_item_id, debug, ttl, client_label)


@router.get("/r/episode/{catalog_item_id}", response_model=RuntimeResolveDebug)
def resolve_episode(
    catalog_item_id: str,
    debug: bool = False,
    ttl: int | None = Query(default=None, ge=1, le=86400),
    client_label: str | None = None,
) -> RuntimeResolveDebug | RedirectResponse:
    return _runtime_response("episode", catalog_item_id, debug, ttl, client_label)
