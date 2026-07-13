from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

from app.schemas.runtime import RuntimePreview, RuntimeResolveDebug
from app.services.logs import add_log
from app.services.runtime import RuntimeResolveUnavailable, mask_runtime_target, preview_runtime, resolve_runtime, runtime_client_fingerprint

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
    request: Request,
    method: str,
    route_media_type: str,
    catalog_item_id: str,
    debug: bool,
    ttl: int | None,
    client_label: str | None,
    client_session: str | None,
    reserve: bool = True,
) -> RuntimeResolveDebug | RedirectResponse:
    forwarded_for = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
    remote_addr = forwarded_for or (request.client.host if request.client else None)
    user_agent = request.headers.get("user-agent")
    identity_session = client_session
    identity_fingerprint = None if identity_session else runtime_client_fingerprint(catalog_item_id, route_media_type, remote_addr, user_agent)
    try:
        payload, raw_location_ref = resolve_runtime(
            route_media_type,
            catalog_item_id,
            ttl,
            client_label,
            client_session=identity_session,
            client_fingerprint=identity_fingerprint,
            reserve=reserve,
        )
    except RuntimeResolveUnavailable as exc:
        raise _runtime_error(exc) from exc
    selected_account = payload.selected_account.get("account_name") or payload.selected_account.get("account_id") or "unknown"
    reservation = payload.broker_decision.reservation
    action = payload.reservation_action
    reuse_reason = payload.reuse_reason or ("new playback reservation" if action == "reservation_created" else "non-reserving probe")
    add_log(
        "info",
        "runtime",
        (
            f"{method} /r/{route_media_type}/{catalog_item_id} {action}; reservation={payload.reservation_id}; "
            f"account={selected_account}; identity_type={reservation.identity_type if reservation else None}; "
            f"identity={reservation.masked_client_identity if reservation else None}; reason={reuse_reason}; redirect={mask_runtime_target(raw_location_ref)}"
        ),
    )
    if debug:
        return payload
    headers = {
        "X-Media-Router-Reservation-Action": action,
        "X-Media-Router-Reservation-Id": payload.reservation_id,
        "X-Media-Router-Selected-Account": str(selected_account),
        "X-Media-Router-Reuse-Reason": reuse_reason,
    }
    return RedirectResponse(raw_location_ref, status_code=302, headers=headers)


@router.get("/r/live/{catalog_item_id}", response_model=RuntimeResolveDebug)
def resolve_live(
    request: Request,
    catalog_item_id: str,
    debug: bool = False,
    ttl: int | None = Query(default=None, ge=1, le=86400),
    client_label: str | None = None,
    client_session: str | None = None,
) -> RuntimeResolveDebug | RedirectResponse:
    return _runtime_response(request, "GET", "live", catalog_item_id, debug, ttl, client_label, client_session)


@router.head("/r/live/{catalog_item_id}")
def resolve_live_head(
    request: Request,
    catalog_item_id: str,
    ttl: int | None = Query(default=None, ge=1, le=86400),
    client_label: str | None = None,
    client_session: str | None = None,
) -> RedirectResponse:
    return _runtime_response(request, "HEAD", "live", catalog_item_id, False, ttl, client_label, client_session, reserve=False)


@router.get("/r/movie/{catalog_item_id}", response_model=RuntimeResolveDebug)
def resolve_movie(
    request: Request,
    catalog_item_id: str,
    debug: bool = False,
    ttl: int | None = Query(default=None, ge=1, le=86400),
    client_label: str | None = None,
    client_session: str | None = None,
) -> RuntimeResolveDebug | RedirectResponse:
    return _runtime_response(request, "GET", "movie", catalog_item_id, debug, ttl, client_label, client_session)


@router.head("/r/movie/{catalog_item_id}")
def resolve_movie_head(
    request: Request,
    catalog_item_id: str,
    ttl: int | None = Query(default=None, ge=1, le=86400),
    client_label: str | None = None,
    client_session: str | None = None,
) -> RedirectResponse:
    return _runtime_response(request, "HEAD", "movie", catalog_item_id, False, ttl, client_label, client_session, reserve=False)


@router.get("/r/episode/{catalog_item_id}", response_model=RuntimeResolveDebug)
def resolve_episode(
    request: Request,
    catalog_item_id: str,
    debug: bool = False,
    ttl: int | None = Query(default=None, ge=1, le=86400),
    client_label: str | None = None,
    client_session: str | None = None,
) -> RuntimeResolveDebug | RedirectResponse:
    return _runtime_response(request, "GET", "episode", catalog_item_id, debug, ttl, client_label, client_session)


@router.head("/r/episode/{catalog_item_id}")
def resolve_episode_head(
    request: Request,
    catalog_item_id: str,
    ttl: int | None = Query(default=None, ge=1, le=86400),
    client_label: str | None = None,
    client_session: str | None = None,
) -> RedirectResponse:
    return _runtime_response(request, "HEAD", "episode", catalog_item_id, False, ttl, client_label, client_session, reserve=False)
