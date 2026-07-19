from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator
from urllib.parse import urlsplit, urlunsplit


class EmbySettingsRead(BaseModel):
    enabled: bool = False
    server_url: str = ""
    has_api_key: bool = False
    poll_interval_seconds: int = Field(default=10, ge=5, le=3600)
    release_grace_seconds: int = Field(default=30, ge=5, le=3600)
    unavailable_timeout_seconds: int = Field(default=60, ge=10, le=86400)
    request_timeout_seconds: int = Field(default=10, ge=1, le=120)
    verify_tls: bool = True
    emby_runtime_correlation_enabled: bool = False


class EmbySettingsUpdate(BaseModel):
    enabled: bool | None = None
    server_url: str | None = None
    api_key: str | None = Field(default=None, max_length=1024)
    poll_interval_seconds: int | None = Field(default=None, ge=5, le=3600)
    release_grace_seconds: int | None = Field(default=None, ge=5, le=3600)
    unavailable_timeout_seconds: int | None = Field(default=None, ge=10, le=86400)
    request_timeout_seconds: int | None = Field(default=None, ge=1, le=120)
    verify_tls: bool | None = None
    emby_runtime_correlation_enabled: bool | None = None

    @field_validator("server_url")
    @classmethod
    def validate_server_url(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return ""
        value = value.strip().rstrip("/")
        parts = urlsplit(value)
        if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
            raise ValueError("Emby server URL must begin with http:// or https://")
        if parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError("Emby server URL must not contain credentials, query parameters, or fragments")
        port = f":{parts.port}" if parts.port else ""
        host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
        path = parts.path.rstrip("/")
        return urlunsplit((parts.scheme.lower(), f"{host}{port}", path, "", ""))


class EmbyConnectionResult(BaseModel):
    success: bool
    health_state: str
    message: str
    server_id: str | None = None
    server_name: str | None = None
    server_version: str | None = None


class EmbyPlaybackSession(BaseModel):
    emby_server_id: str | None = None
    emby_session_id: str
    emby_play_session_id: str | None = None
    emby_device_id: str | None = None
    device_name: str | None = None
    client_name: str | None = None
    emby_user_id: str | None = None
    user_name: str | None = None
    emby_item_id: str | None = None
    item_name: str | None = None
    item_type: str | None = None
    emby_media_source_id: str | None = None
    live_stream_id: str | None = None
    emby_channel_id: str | None = None
    emby_program_id: str | None = None
    emby_provider_ids: dict[str, str] = Field(default_factory=dict)
    emby_tuner_source_ids: list[str] = Field(default_factory=list)
    catalog_identity_candidates: list[str] = Field(default_factory=list)
    playback_position_ticks: int = 0
    playback_state: Literal["playing", "paused"] = "playing"
    catalog_item_id: str | None = None
    media_type: str | None = None
    runtime_path: str | None = None
    original_media_path_present: bool = False
    direct_catalog_identity_available: bool = False
    recent_runtime_observation_found: bool = False
    correlation_candidate_count: int = 0
    unmatched_reason: str | None = None
    rejected_for_age_count: int = 0
    rejected_for_media_type_count: int = 0
    rejected_for_lifecycle_count: int = 0
    rejected_for_client_context_count: int = 0
    observed_at: datetime
    reservation_id: str | None = None
    correlation_method: str | None = None
    correlation_confidence: str | None = None
    binding_status: str | None = None


class EmbyPlaybackBinding(BaseModel):
    id: str
    emby_server_id: str
    emby_session_id: str
    emby_play_session_id: str | None = None
    emby_device_id: str | None = None
    emby_user_id: str | None = None
    emby_item_id: str | None = None
    emby_media_source_id: str | None = None
    reservation_id: str
    catalog_item_id: str
    media_type: str
    playback_state: str
    first_observed_at: datetime
    last_observed_at: datetime
    last_confirmed_playing_at: datetime | None = None
    missing_since: datetime | None = None
    released_at: datetime | None = None
    release_reason: str | None = None
    correlation_method: str
    correlation_confidence: str
    device_name: str | None = None
    client_name: str | None = None
    user_name: str | None = None
    item_name: str | None = None
    created_at: datetime
    updated_at: datetime


class EmbyIntegrationStatus(BaseModel):
    enabled: bool
    configured: bool
    health_state: str
    last_poll_attempt: datetime | None = None
    last_successful_poll: datetime | None = None
    consecutive_failures: int = 0
    server_id: str | None = None
    server_name: str | None = None
    server_version: str | None = None
    observed_playback_count: int = 0
    matched_playback_count: int = 0
    unmatched_playback_count: int = 0
    active_binding_count: int = 0
    pending_release_count: int = 0
    last_error: str | None = None


class EmbyChannelMapping(BaseModel):
    integration_id: str
    emby_server_id: str
    emby_item_id: str
    emby_media_source_id: str | None = None
    emby_channel_name: str | None = None
    catalog_item_id: str | None = None
    mapping_source: str
    created_at: datetime
    updated_at: datetime


class EmbyChannelMappingUpdate(BaseModel):
    catalog_item_id: str
    emby_media_source_id: str | None = None


class EmbyChannelRefreshResult(BaseModel):
    discovered: int
    mapped: int
    unmapped: int


class EmbyChannelMappingPreviewItem(BaseModel):
    integration_id: str
    emby_item_id: str
    emby_media_source_id: str | None = None
    emby_channel_name: str
    catalog_item_id: str | None = None
    match_source: str
    status: str
    detail: str


class EmbyChannelMappingPreview(BaseModel):
    integration_id: str
    automatic_matches: int
    manual_matches: int = 0
    ambiguous: int
    unmatched: int
    conflicts: int
    items: list[EmbyChannelMappingPreviewItem]


class EmbyChannelMappingPage(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[EmbyChannelMapping]
