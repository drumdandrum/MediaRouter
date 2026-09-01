import sqlite3

from fastapi import APIRouter, HTTPException, Query

from app.core.redaction import redact_text

from app.schemas.integrations import (
    EmbyConnectionResult, EmbyIntegrationStatus, EmbyPlaybackBinding,
    EmbyPlaybackSession, EmbySettingsRead, EmbySettingsUpdate,
    EmbyChannelMapping, EmbyChannelMappingUpdate, EmbyChannelRefreshResult,
    EmbyChannelMappingPreview, EmbyChannelMappingPage,
    EmbyMappingAuditRequest, EmbyMappingAuditResponse,
    EmbyVodItemMapping, EmbyVodItemMappingUpdate,
)
from app.services.emby import (
    EmbyError,
    get_emby_settings, get_emby_status, list_emby_bindings,
    list_observed_sessions, test_emby_connection, update_emby_settings,
    link_emby_channel, list_emby_channel_mappings, refresh_emby_channel_mappings,
    delete_emby_channel_mapping,
    page_emby_channel_mappings, preview_emby_channel_mappings,
    delete_emby_vod_item_mapping, get_emby_vod_item_mapping,
    link_emby_vod_item, list_emby_vod_item_mappings,
)
from app.services.emby_audit import preview_emby_mapping_audit


router = APIRouter(prefix="/api/integrations", tags=["integrations"])


@router.get("/emby", response_model=EmbySettingsRead)
def emby_settings() -> EmbySettingsRead:
    return get_emby_settings()


@router.put("/emby", response_model=EmbySettingsRead)
def emby_settings_update(payload: EmbySettingsUpdate) -> EmbySettingsRead:
    return update_emby_settings(payload)


@router.post("/emby/test", response_model=EmbyConnectionResult)
def emby_test() -> EmbyConnectionResult:
    return test_emby_connection()


@router.get("/emby/status", response_model=EmbyIntegrationStatus)
def emby_status() -> EmbyIntegrationStatus:
    return get_emby_status()


@router.get("/emby/sessions", response_model=list[EmbyPlaybackSession])
def emby_sessions(limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)) -> list[EmbyPlaybackSession]:
    return list_observed_sessions(limit, offset)


@router.get("/emby/bindings", response_model=list[EmbyPlaybackBinding])
def emby_bindings(limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)) -> list[EmbyPlaybackBinding]:
    return list_emby_bindings(limit, offset)


@router.get("/emby/channel-mappings", response_model=list[EmbyChannelMapping])
def emby_channel_mappings() -> list[EmbyChannelMapping]:
    return list_emby_channel_mappings()


@router.get("/emby/channel-mappings/page", response_model=EmbyChannelMappingPage)
def emby_channel_mappings_page(limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0), search: str = "") -> EmbyChannelMappingPage:
    return page_emby_channel_mappings(limit, offset, search)


@router.post("/emby/channel-mappings/preview", response_model=EmbyChannelMappingPreview)
def emby_channel_mappings_preview() -> EmbyChannelMappingPreview:
    try:
        return preview_emby_channel_mappings()
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Emby channel mapping preview failed") from exc


@router.post(
    "/emby/mapping-audit/preview",
    response_model=EmbyMappingAuditResponse,
)
def emby_mapping_audit_preview(
    payload: EmbyMappingAuditRequest,
) -> EmbyMappingAuditResponse:
    try:
        return preview_emby_mapping_audit(payload)
    except EmbyError as exc:
        if exc.health_state == "not_configured":
            raise HTTPException(
                status_code=409,
                detail="Emby integration is not configured.",
            ) from exc
        raise HTTPException(
            status_code=502,
            detail="Emby mapping audit could not retrieve the remote library.",
        ) from exc
    except (LookupError, sqlite3.Error) as exc:
        raise HTTPException(
            status_code=503,
            detail="Media Router catalog data is unavailable for audit.",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="Emby mapping audit failed safely.",
        ) from exc


@router.post("/emby/channel-mappings/refresh", response_model=EmbyChannelRefreshResult)
def emby_channel_mappings_refresh() -> EmbyChannelRefreshResult:
    try:
        return refresh_emby_channel_mappings()
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Emby channel refresh failed") from exc


@router.put("/emby/channel-mappings/{emby_server_id}/{emby_item_id}", response_model=EmbyChannelMapping)
def emby_channel_mapping_update(emby_server_id: str, emby_item_id: str, payload: EmbyChannelMappingUpdate) -> EmbyChannelMapping:
    try:
        return link_emby_channel(emby_server_id, emby_item_id, payload.catalog_item_id, payload.emby_media_source_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=redact_text(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=redact_text(exc)) from exc


@router.delete("/emby/channel-mappings/{integration_id}/{emby_item_id}", status_code=204)
def emby_channel_mapping_delete(integration_id: str, emby_item_id: str) -> None:
    if not delete_emby_channel_mapping(integration_id, emby_item_id):
        raise HTTPException(status_code=404, detail="Emby channel mapping not found")


@router.get("/emby/vod-item-mappings", response_model=list[EmbyVodItemMapping])
def emby_vod_item_mappings() -> list[EmbyVodItemMapping]:
    return list_emby_vod_item_mappings()


@router.get("/emby/vod-item-mappings/{integration_id}/{emby_item_id}", response_model=EmbyVodItemMapping)
def emby_vod_item_mapping(integration_id: str, emby_item_id: str) -> EmbyVodItemMapping:
    try:
        mapping = get_emby_vod_item_mapping(integration_id, emby_item_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=redact_text(exc)) from exc
    if mapping is None:
        raise HTTPException(status_code=404, detail="Emby VOD item mapping not found")
    return mapping


@router.put("/emby/vod-item-mappings/{integration_id}/{emby_item_id}", response_model=EmbyVodItemMapping)
def emby_vod_item_mapping_update(integration_id: str, emby_item_id: str,
                                 payload: EmbyVodItemMappingUpdate) -> EmbyVodItemMapping:
    try:
        return link_emby_vod_item(integration_id, emby_item_id, payload.catalog_id,
                                  payload.media_type, payload.emby_media_source_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=redact_text(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=redact_text(exc)) from exc


@router.delete("/emby/vod-item-mappings/{integration_id}/{emby_item_id}", status_code=204)
def emby_vod_item_mapping_delete(integration_id: str, emby_item_id: str) -> None:
    try:
        deleted = delete_emby_vod_item_mapping(integration_id, emby_item_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=redact_text(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="Emby VOD item mapping not found")
