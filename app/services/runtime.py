from __future__ import annotations

from app.schemas.broker import BrokerErrorDetail
from app.schemas.catalog import CatalogItem
from app.schemas.runtime import RuntimeErrorDetail, RuntimePreview, RuntimeResolveDebug
from app.core.config import get_settings
from app.services.broker import BrokerUnavailable, get_raw_reservation_location_ref, resolve_source
from app.services.catalog import count_enabled_source_availability, get_item
from app.services.settings import get_app_settings


ROUTE_TO_MEDIA_TYPE = {
    "live": "channel",
    "movie": "movie",
    "episode": "episode",
}
MEDIA_TYPE_TO_ROUTE = {
    "channel": "live",
    "movie": "movie",
    "episode": "episode",
}


class RuntimeResolveUnavailable(Exception):
    def __init__(
        self,
        status_code: int,
        failure_code: str,
        failure_message: str,
        decision_reasons: list[str] | None = None,
        evaluated_candidates: list | None = None,
    ) -> None:
        super().__init__(failure_message)
        self.status_code = status_code
        self.detail = RuntimeErrorDetail(
            failure_code=failure_code,
            failure_message=failure_message,
            decision_reasons=decision_reasons or [],
            evaluated_candidates=evaluated_candidates or [],
        )


def _clean_base_url(value: str | None) -> str:
    return (value or "").strip().rstrip("/")


def _is_docker_internal_base_url(value: str) -> bool:
    lowered = value.lower()
    return lowered in {"http://media-router:8088", "https://media-router:8088"} or lowered.startswith(("http://media-router/", "https://media-router/"))


def public_runtime_base_url(request_base_url: str | None = None) -> str:
    app_settings = get_app_settings()
    configured_runtime = _clean_base_url(app_settings.runtime_public_base_url)
    if configured_runtime:
        return configured_runtime

    env_public = _clean_base_url(get_settings().public_base_url)
    if env_public and not _is_docker_internal_base_url(env_public):
        return env_public

    request_base = _clean_base_url(request_base_url)
    if request_base:
        return request_base

    return "http://localhost:8088"


def route_for_media_type(media_type: str) -> str | None:
    return MEDIA_TYPE_TO_ROUTE.get(media_type)


def runtime_url_for(catalog_item: CatalogItem, debug: bool = False, request_base_url: str | None = None) -> str:
    route = route_for_media_type(catalog_item.media_type)
    if route is None:
        raise RuntimeResolveUnavailable(
            404,
            "catalog_item_not_found",
            "Catalog item does not have a Sprint 5 runtime route.",
        )
    suffix = "?debug=true" if debug else ""
    return f"{public_runtime_base_url(request_base_url)}/r/{route}/{catalog_item.internal_id}{suffix}"


def preview_runtime(catalog_item_id: str, request_base_url: str | None = None) -> RuntimePreview:
    catalog_item = get_item(catalog_item_id)
    if catalog_item is None or route_for_media_type(catalog_item.media_type) is None:
        raise RuntimeResolveUnavailable(
            404,
            "catalog_item_not_found",
            "Catalog item was not found for runtime resolution.",
        )
    return RuntimePreview(
        catalog_item=catalog_item,
        media_type=catalog_item.media_type,
        runtime_url=runtime_url_for(catalog_item, request_base_url=request_base_url),
        debug_url=runtime_url_for(catalog_item, debug=True, request_base_url=request_base_url),
        available_source_count=count_enabled_source_availability(catalog_item.internal_id),
    )


def _validate_route(catalog_item: CatalogItem | None, route_media_type: str) -> CatalogItem:
    expected = ROUTE_TO_MEDIA_TYPE[route_media_type]
    if catalog_item is None or catalog_item.media_type != expected:
        raise RuntimeResolveUnavailable(
            404,
            "catalog_item_not_found",
            f"No {route_media_type} catalog item was found for this runtime URL.",
        )
    return catalog_item


def resolve_runtime(
    route_media_type: str,
    catalog_item_id: str,
    ttl: int | None = None,
    client_label: str | None = None,
) -> tuple[RuntimeResolveDebug, str]:
    catalog_item = _validate_route(get_item(catalog_item_id), route_media_type)
    try:
        decision = resolve_source(
            catalog_item_id=catalog_item.internal_id,
            media_type=route_media_type,
            client_label=client_label,
            reservation_ttl_seconds=ttl,
        )
    except BrokerUnavailable as exc:
        detail: BrokerErrorDetail = exc.detail
        raise RuntimeResolveUnavailable(
            409,
            detail.failure_code,
            detail.failure_message,
            detail.decision_reasons,
            detail.evaluated_candidates,
        ) from exc

    if decision.selected_source is None or decision.reservation is None or decision.expires_at is None:
        raise RuntimeResolveUnavailable(409, "no_sources", "No source selected.")

    raw_location_ref = get_raw_reservation_location_ref(decision.reservation.reservation_id)
    if not raw_location_ref:
        raise RuntimeResolveUnavailable(409, "no_sources", "Selected source location was not available.")

    selected = decision.selected_source
    debug_payload = RuntimeResolveDebug(
        catalog_item=catalog_item,
        selected_provider={
            "provider_id": selected.provider_id,
            "provider_name": selected.provider_name,
            "provider_type": selected.provider_type,
        },
        selected_account={
            "account_id": selected.account_id,
            "account_name": selected.account_name,
            "priority_group": selected.priority_group,
            "weight": selected.weight,
            "active_reservations_before_reserve": selected.active_reservations,
            "max_simultaneous_streams": selected.max_simultaneous_streams,
        },
        selected_source=selected,
        reservation_id=decision.reservation.reservation_id,
        expires_at=decision.expires_at,
        stream_url=decision.stream_url or selected.location_ref,
        broker_decision=decision,
        evaluated_candidates=decision.evaluated_candidates,
    )
    return debug_payload, raw_location_ref
