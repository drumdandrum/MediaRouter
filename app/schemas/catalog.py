from datetime import datetime

from pydantic import BaseModel, Field


class CatalogSummary(BaseModel):
    channels: int
    movies: int
    series: int
    episodes: int
    sources: int
    last_import_time: datetime | None = None


class CatalogItem(BaseModel):
    internal_id: str
    media_type: str
    title: str
    group_title: str | None = None
    tvg_id: str | None = None
    tvg_name: str | None = None
    tvg_logo: str | None = None
    cuid: str | None = None
    show_name: str | None = None
    season_number: int | None = None
    episode_number: int | None = None
    episode_title: str | None = None
    confidence: str
    created_at: datetime
    updated_at: datetime


class CatalogSource(BaseModel):
    id: int
    catalog_internal_id: str
    media_type: str
    source_name: str
    source_url: str
    cuid: str | None = None
    tvg_id: str | None = None
    raw_extinf: str
    first_seen_at: datetime
    last_seen_at: datetime


class SourceAvailability(BaseModel):
    id: int
    catalog_internal_id: str
    catalog_title: str | None = None
    provider_id: str | None = None
    provider_name: str | None = None
    provider_type: str | None = None
    account_id: str | None = None
    account_name: str | None = None
    priority_group: str | None = None
    weight: int | None = None
    account_enabled: bool | None = None
    account_health_status: str | None = None
    external_id: str | None = None
    location_ref: str
    media_type: str
    enabled: bool
    last_seen_at: datetime
    metadata_confidence: str
    notes: str


class SourceAvailabilityUpdate(BaseModel):
    enabled: bool | None = None
    metadata_confidence: str | None = None
    notes: str | None = None


class CatalogImportRequest(BaseModel):
    paths: list[str] = Field(default_factory=list)
    playlist: str | None = None
    source_name: str = "Manual Import"
    provider_id: str | None = None
    account_id: str | None = None
    media_type: str | None = None


class CatalogImportAccepted(BaseModel):
    job_id: str
    status: str
    message: str
