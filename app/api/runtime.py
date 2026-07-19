import hashlib

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

from app.schemas.runtime import RuntimePreview, RuntimeResolveDebug
from app.services.logs import add_log
from app.services.emby import record_runtime_correlation_observation
from app.services.runtime import RuntimeResolveUnavailable, mask_runtime_target, preview_runtime, resolve_runtime, runtime_client_context, runtime_client_fingerprint
from app.services.broker import consume_ticketed_reservation
from app.services.playback_tickets import PlaybackTicketError, validate_playback_ticket

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
    ticket: str | None = None,
    reserve: bool = True,
) -> RuntimeResolveDebug | RedirectResponse:
    if ticket:
        try:
            claims = validate_playback_ticket(ticket)
            if claims["c"] != catalog_item_id:
                raise PlaybackTicketError("catalog_mismatch", "Playback ticket does not match this catalog item.", 409)
            expected_media_type = {"live": "channel", "movie": "movie", "episode": "episode"}[route_media_type]
            reservation, raw_location_ref = consume_ticketed_reservation(str(claims["r"]), catalog_item_id, expected_media_type)
        except PlaybackTicketError as exc:
            add_log("warning", "runtime", f"reservation_ticket_rejected reason={exc.code} catalog_item={catalog_item_id} ticket=[redacted]")
            raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": str(exc)}) from exc
        except LookupError as exc:
            add_log("warning", "runtime", f"reservation_ticket_rejected reason=missing_reservation catalog_item={catalog_item_id} ticket=[redacted]")
            raise HTTPException(status_code=404, detail={"code": "missing_reservation", "message": "Playback reservation was not found."}) from exc
        except ValueError as exc:
            code = str(exc)
            status = 409 if code == "catalog_mismatch" else 410
            add_log("warning", "runtime", f"reservation_ticket_rejected reason={code} catalog_item={catalog_item_id} ticket=[redacted]")
            raise HTTPException(status_code=status, detail={"code": code, "message": "Playback reservation is no longer available."}) from exc
        add_log("info", "runtime", f"reservation_ticket_validated reservation={reservation.reservation_id} catalog_item={catalog_item_id} ticket=[redacted]")
        add_log("info", "runtime", f"ticketed_reservation_reused reservation={reservation.reservation_id} catalog_item={catalog_item_id} account={reservation.account_name or reservation.account_id} redirect={mask_runtime_target(raw_location_ref)}")
        return RedirectResponse(raw_location_ref, status_code=302, headers={
            "X-Media-Router-Reservation-Action": "ticketed_reservation_reused",
            "X-Media-Router-Reservation-Id": reservation.reservation_id,
            "X-Media-Router-Selected-Account": str(reservation.account_name or reservation.account_id),
            "X-Media-Router-Reuse-Reason": "Validated broker playback ticket.",
        })
    peer_addr = request.client.host if request.client else None
    context = runtime_client_context(peer_addr, request.headers)
    remote_addr = str(context["address"])
    user_agent = request.headers.get("user-agent")
    identity_session = client_session
    identity_fingerprint = None if identity_session else runtime_client_fingerprint(catalog_item_id, route_media_type, remote_addr, user_agent)
    fingerprint_signature = hashlib.sha256(
        f"{context['address_signature']}|{context['user_agent_signature']}|{route_media_type}|{catalog_item_id}".encode("utf-8")
    ).hexdigest()[:12]
    add_log("info", "runtime", (
        f"fingerprint_inputs catalog_item={catalog_item_id} media_type={route_media_type} method={method} "
        f"address_source={'explicit_client_session' if identity_session else context['address_source']} "
        f"address_signature={context['address_signature']} ua_family={context['user_agent_family']} "
        f"ua_signature={context['user_agent_signature']} "
        f"range_present={bool(request.headers.get('range'))} stable_emby_headers={','.join(context['stable_header_names']) or 'none'} "
        f"input_signature={fingerprint_signature}"
    ))
    try:
        payload, raw_location_ref = resolve_runtime(
            route_media_type,
            catalog_item_id,
            ttl,
            client_label,
            client_session=identity_session,
            client_fingerprint=identity_fingerprint,
            origin_identity=str(context["origin_identity"]),
            stable_client_id=str(context["stable_client_id"]) if context["stable_client_id"] else None,
            request_profile=str(context["user_agent_family"]),
            reserve=reserve,
            meaningful_activity=method == "GET",
        )
    except RuntimeResolveUnavailable as exc:
        raise _runtime_error(exc) from exc
    selected_account = payload.selected_account.get("account_name") or payload.selected_account.get("account_id") or "unknown"
    reservation = payload.broker_decision.reservation
    action = payload.reservation_action
    reuse_reason = payload.reuse_reason or ("new playback reservation" if action == "reservation_created" else "non-reserving probe")
    if reserve and method == "GET":
        observation_identity_type = ("explicit_session" if identity_session else
            "stable_client_id" if context["stable_client_id"] else "derived_fingerprint")
        observation_identity = identity_session or context["stable_client_id"] or identity_fingerprint
        try:
            record_runtime_correlation_observation(
                reservation_id=payload.reservation_id, catalog_item_id=catalog_item_id,
                media_type=route_media_type, route_type=route_media_type,
                request_identity_type=observation_identity_type,
                request_identity=str(observation_identity) if observation_identity else None,
                stable_emby_identifier=str(context["stable_client_id"]) if context["stable_client_id"] else None,
                request_profile=str(context["user_agent_family"]),
                address_signature=str(context["address_signature"]),
                user_agent_signature=str(context["user_agent_signature"]),
            )
        except Exception as exc:
            add_log("warning", "runtime", f"correlation_observation_failed reservation={payload.reservation_id} error={type(exc).__name__}")
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
    ticket: str | None = None,
) -> RuntimeResolveDebug | RedirectResponse:
    return _runtime_response(request, "GET", "live", catalog_item_id, debug, ttl, client_label, client_session, ticket)


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
    ticket: str | None = None,
) -> RuntimeResolveDebug | RedirectResponse:
    return _runtime_response(request, "GET", "movie", catalog_item_id, debug, ttl, client_label, client_session, ticket)


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
    ticket: str | None = None,
) -> RuntimeResolveDebug | RedirectResponse:
    return _runtime_response(request, "GET", "episode", catalog_item_id, debug, ttl, client_label, client_session, ticket)


@router.head("/r/episode/{catalog_item_id}")
def resolve_episode_head(
    request: Request,
    catalog_item_id: str,
    ttl: int | None = Query(default=None, ge=1, le=86400),
    client_label: str | None = None,
    client_session: str | None = None,
) -> RedirectResponse:
    return _runtime_response(request, "HEAD", "episode", catalog_item_id, False, ttl, client_label, client_session, reserve=False)
