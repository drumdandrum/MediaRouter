from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


OutputAction = Literal["create", "update", "skip", "remove", "fail"]
OutputMode = Literal["dry_run", "generate"]


class StrmSettings(BaseModel):
    movies_output_directory: str = "/outputs/movies"
    series_output_directory: str = "/outputs/series"
    filename_format: str = "default"
    overwrite_existing_files: bool = True
    remove_orphaned_files: bool = False
    dry_run_mode: bool = False


class StrmSettingsUpdate(BaseModel):
    movies_output_directory: str | None = Field(default=None, min_length=1)
    series_output_directory: str | None = Field(default=None, min_length=1)
    filename_format: str | None = None
    overwrite_existing_files: bool | None = None
    remove_orphaned_files: bool | None = None
    dry_run_mode: bool | None = None


class StrmOutputOperation(BaseModel):
    action: OutputAction
    media_type: str
    catalog_item_id: str | None = None
    title: str | None = None
    output_path: str
    runtime_url: str | None = None
    reason: str


class StrmOutputSummary(BaseModel):
    mode: OutputMode
    created_count: int = 0
    updated_count: int = 0
    skipped_count: int = 0
    removed_count: int = 0
    failed_count: int = 0
    movie_count: int = 0
    episode_count: int = 0
    output_paths: list[str] = []
    duration_seconds: float = 0


class StrmOutputResult(BaseModel):
    settings: StrmSettings
    runtime_base_url: str
    summary: StrmOutputSummary
    operations: list[StrmOutputOperation]


class OutputPathValidation(BaseModel):
    path: str
    purpose: str
    exists: bool
    readable: bool
    writable: bool
    can_create: bool
    status: str
    message: str


class OutputPathValidationResult(BaseModel):
    paths: list[OutputPathValidation]
    can_generate: bool


class GeneratedOutputFile(BaseModel):
    output_id: str
    catalog_item_id: str
    media_type: str
    output_type: str
    output_path: str
    last_content_hash: str
    last_generated_at: datetime
    status: str


class OutputRunHistory(BaseModel):
    output_id: str
    output_type: str
    mode: OutputMode
    status: str
    summary: StrmOutputSummary
    started_at: datetime
    finished_at: datetime | None = None


class LiveM3uSettings(BaseModel):
    output_file_path: str = "/outputs/live/live.m3u"
    runtime_client_access_url: str = ""
    include_disabled_channels: bool = False
    include_logos: bool = True
    include_group_title: bool = True
    include_tvg_id: bool = True
    include_tvg_name: bool = True
    channel_limit: int = Field(default=0, ge=0, le=5000)
    dry_run_mode: bool = False


class LiveM3uSettingsUpdate(BaseModel):
    output_file_path: str | None = Field(default=None, min_length=1)
    runtime_client_access_url: str | None = None
    include_disabled_channels: bool | None = None
    include_logos: bool | None = None
    include_group_title: bool | None = None
    include_tvg_id: bool | None = None
    include_tvg_name: bool | None = None
    channel_limit: int | None = Field(default=None, ge=0, le=5000)
    dry_run_mode: bool | None = None


class LiveM3uOutputSummary(BaseModel):
    mode: OutputMode
    total_live_channels: int = 0
    written_count: int = 0
    skipped_count: int = 0
    created_count: int = 0
    updated_count: int = 0
    failed_count: int = 0
    output_path: str
    duration_seconds: float = 0


class LiveM3uPreviewEntry(BaseModel):
    catalog_item_id: str
    title: str
    channel_number: str | None = None
    group_title: str | None = None
    extinf: str
    runtime_url: str


class LiveM3uOutputResult(BaseModel):
    settings: LiveM3uSettings
    runtime_base_url: str
    summary: LiveM3uOutputSummary
    preview_entries: list[LiveM3uPreviewEntry]
    skipped_channels: list[str] = []


class LiveM3uRunHistory(BaseModel):
    output_id: str
    output_type: str
    mode: OutputMode
    status: str
    summary: LiveM3uOutputSummary
    started_at: datetime
    finished_at: datetime | None = None
