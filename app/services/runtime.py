from __future__ import annotations

import hashlib
import ipaddress
import re
from urllib.parse import urlsplit, urlunsplit

from app.schemas.broker import BrokerErrorDetail
from app.schemas.catalog import CatalogItem
from app.schemas.runtime import RuntimeErrorDetail, RuntimePreview, RuntimeResolveDebug
from app.core.config import get_settings
from app.services.broker import DEFAULT_REUSE_WINDOW_SECONDS, BrokerUnavailable, get_raw_reservation_location_ref, resolve_source
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
RUNTIME_DEFAULT_TTL_SECONDS = {"live": 14400, "movie": 10800, "episode": 7200}


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


def mask_runtime_target(value: str) -> str:
    try:
        parts = urlsplit(value)
    except ValueError:
        return "[invalid-url]"
    netloc = parts.netloc
    if "@" in netloc:
        host = netloc.rsplit("@", 1)[1]
        netloc = f"[redacted]@{host}"
    path = re.sub(r"(/(?:live|movie|series)/)[^/]+/[^/]+/", r"\1[redacted]/[redacted]/", parts.path, flags=re.IGNORECASE)
    path = re.sub(r"(/)([^/?#]{4,})/([^/?#]{4,})(/[^/?#]+)$", r"/[redacted]/[redacted]\4", path)
    query = "[redacted]" if parts.query else ""
    return urlunsplit((parts.scheme, netloc, path, query, ""))


def normalize_client_ip(remote_addr: str | None) -> str:
    value = (remote_addr or "unknown").strip().lower()
    if value.startswith("[") and "]" in value:
        value = value[1:value.index("]")]
    elif value.count(":") == 1 and value.rsplit(":", 1)[1].isdigit():
        value = value.rsplit(":", 1)[0]
    value = value.split("%", 1)[0]
    try:
        return ipaddress.ip_address(value).compressed
    except ValueError:
        return "unknown"


def normalize_user_agent(user_agent: str | None) -> str:
    value = re.sub(r"\s+", " ", (user_agent or "unknown").strip().lower())
    return re.sub(r"(?<=/)[0-9]+(?:\.[0-9]+)+", "*", value)[:256]


def user_agent_family(user_agent: str | None) -> str:
    normalized = normalize_user_agent(user_agent)
    if "emby" in normalized:
        if any(token in normalized for token in ("ffmpeg", "lavf", "probe")):
            return "emby_ffmpeg_probe"
        if any(token in normalized for token in ("playback", "transcode", "worker")):
            return "emby_playback_worker"
        return "emby_server"
    if "jellyfin" in normalized:
        return "jellyfin"
    if "vlc" in normalized:
        return "vlc"
    if "ffmpeg" in normalized or "lavf" in normalized:
        return "generic_ffmpeg"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]


def runtime_client_fingerprint(catalog_item_id: str, media_type: str, remote_addr: str | None, user_agent: str | None) -> str:
    family = user_agent_family(user_agent)
    agent_component = family if family.startswith(("emby_", "jellyfin", "vlc")) else normalize_user_agent(user_agent)
    raw = "|".join([catalog_item_id, media_type, normalize_client_ip(remote_addr), agent_component])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _trusted_proxy_peer(peer_addr: str | None, networks: str) -> bool:
    peer = normalize_client_ip(peer_addr)
    if peer == "unknown":
        return False
    try:
        address = ipaddress.ip_address(peer)
        return any(address in ipaddress.ip_network(value.strip(), strict=False)
                   for value in networks.split(",") if value.strip())
    except ValueError:
        return False


def runtime_client_context(peer_addr: str | None, headers) -> dict[str, str | list[str] | None]:
    settings = get_app_settings()
    address = peer_addr
    source = "direct_peer_address"
    header_name = settings.trusted_proxy_client_header.strip().lower()
    if (settings.trust_proxy_headers and _trusted_proxy_peer(peer_addr, settings.trusted_proxy_networks)
            and header_name in {"x-forwarded-for", "cf-connecting-ip", "x-real-ip"}):
        forwarded = (headers.get(header_name) or "").strip()
        if header_name == "x-forwarded-for":
            forwarded = forwarded.split(",", 1)[0].strip()
        if forwarded:
            address = forwarded
            source = {"x-forwarded-for": "trusted_x_forwarded_for",
                      "cf-connecting-ip": "cf_connecting_ip",
                      "x-real-ip": "trusted_x_real_ip"}[header_name]
    normalized_address = normalize_client_ip(address)
    stable_header_names = [name for name in ("x-emby-device-id", "x-emby-session-id", "x-emby-client")
                           if headers.get(name)]
    stable_client_value = headers.get("x-emby-device-id") or headers.get("x-emby-session-id")
    normalized_agent = normalize_user_agent(headers.get("user-agent"))
    agent_family = user_agent_family(headers.get("user-agent"))
    if stable_header_names and agent_family == "generic_ffmpeg":
        agent_family = "emby_ffmpeg_probe"
    return {
        "address": normalized_address,
        "address_source": source,
        "origin_identity": hashlib.sha256(f"origin:{normalized_address}".encode("utf-8")).hexdigest()[:32],
        "address_signature": hashlib.sha256(normalized_address.encode("utf-8")).hexdigest()[:8],
        "user_agent_family": agent_family,
        "user_agent_signature": hashlib.sha256(normalized_agent.encode("utf-8")).hexdigest()[:8],
        "stable_client_id": stable_client_value,
        "stable_header_names": stable_header_names,
    }


def runtime_client_address(peer_addr: str | None, headers) -> str | None:
    return str(runtime_client_context(peer_addr, headers)["address"])


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
    client_session: str | None = None,
    client_fingerprint: str | None = None,
    origin_identity: str | None = None,
    stable_client_id: str | None = None,
    request_profile: str | None = None,
    reserve: bool = True,
    meaningful_activity: bool = True,
) -> tuple[RuntimeResolveDebug, str]:
    catalog_item = _validate_route(get_item(catalog_item_id), route_media_type)
    runtime_settings = get_app_settings()
    effective_ttl = ttl or getattr(runtime_settings, f"{route_media_type}_active_ttl_seconds")
    try:
        decision = resolve_source(
            catalog_item_id=catalog_item.internal_id,
            media_type=route_media_type,
            client_label=client_label,
            client_session=client_session,
            client_fingerprint=client_fingerprint,
            origin_identity=origin_identity,
            stable_client_id=stable_client_id,
            request_profile=request_profile,
            startup_coalescing_window_seconds=get_app_settings().startup_coalescing_window_seconds,
            allow_reservation_reuse=True,
            reuse_window_seconds=DEFAULT_REUSE_WINDOW_SECONDS,
            reserve=reserve,
            reservation_ttl_seconds=effective_ttl,
            lifecycle_enabled=reserve,
            meaningful_activity=meaningful_activity,
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

    raw_location_ref = get_raw_reservation_location_ref(decision.reservation.reservation_id) if (reserve or decision.reservation_reused) else decision.stream_url
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
        reservation_action="reservation_reused" if decision.reservation_reused else "reservation_created" if decision.reservation_created else "reservation_probe",
        reuse_reason=decision.reuse_reason,
        broker_decision=decision,
        evaluated_candidates=decision.evaluated_candidates,
    )
    return debug_payload, raw_location_ref
