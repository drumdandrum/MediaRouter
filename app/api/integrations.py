from fastapi import APIRouter, HTTPException, Query

from app.schemas.integrations import (
    EmbyConnectionResult, EmbyIntegrationStatus, EmbyPlaybackBinding,
    EmbyPlaybackSession, EmbySettingsRead, EmbySettingsUpdate,
    EmbyChannelMapping, EmbyChannelMappingUpdate, EmbyChannelRefreshResult,
    EmbyChannelMappingPreview, EmbyChannelMappingPage,
)
from app.services.emby import (
    get_emby_settings, get_emby_status, list_emby_bindings,
    list_observed_sessions, test_emby_connection, update_emby_settings,
    link_emby_channel, list_emby_channel_mappings, refresh_emby_channel_mappings,
    delete_emby_channel_mapping,
    page_emby_channel_mappings, preview_emby_channel_mappings,
)


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
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/emby/channel-mappings/{integration_id}/{emby_item_id}", status_code=204)
def emby_channel_mapping_delete(integration_id: str, emby_item_id: str) -> None:
    if not delete_emby_channel_mapping(integration_id, emby_item_id):
        raise HTTPException(status_code=404, detail="Emby channel mapping not found")
