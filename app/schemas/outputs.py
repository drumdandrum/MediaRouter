from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


OutputAction = Literal["create", "update", "skip", "remove", "fail"]
OutputMode = Literal["dry_run", "generate"]
GenerationMode = Literal["Test", "Small", "Medium", "Unlimited", "Custom"]


class StrmSettings(BaseModel):
    movies_output_directory: str = "/outputs/movies"
    series_output_directory: str = "/outputs/series"
    filename_format: str = "default"
    overwrite_existing_files: bool = True
    remove_orphaned_files: bool = False
    dry_run_mode: bool = False
    generation_mode: GenerationMode = "Test"
    maximum_movies: int = Field(default=500, ge=0)
    maximum_episodes: int = Field(default=500, ge=0)
    batch_size: int = Field(default=250, ge=50, le=500)
    worker_count: int = Field(default=4, ge=1, le=16)


class StrmSettingsUpdate(BaseModel):
    movies_output_directory: str | None = Field(default=None, min_length=1)
    series_output_directory: str | None = Field(default=None, min_length=1)
    filename_format: str | None = None
    overwrite_existing_files: bool | None = None
    remove_orphaned_files: bool | None = None
    dry_run_mode: bool | None = None
    generation_mode: GenerationMode | None = None
    maximum_movies: int | None = Field(default=None, ge=0)
    maximum_episodes: int | None = Field(default=None, ge=0)
    batch_size: int | None = Field(default=None, ge=50, le=500)
    worker_count: int | None = Field(default=None, ge=1, le=16)


class StrmGenerateRequest(BaseModel):
    confirm_unlimited: bool = False


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
    total_items: int = 0
    processed_items: int = 0
    excluded_by_limits: int = 0
    capped: bool = True
    generation_mode: GenerationMode = "Test"
    batch_size: int = 250
    percentage_complete: int = 0
    current_media_type: str | None = None
    current_batch: int = 0
    worker_count: int = 4
    items_per_second: float = 0
    average_ms_per_item: float = 0


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
    generation_mode: GenerationMode = "Test"
    maximum_live_channels: int = Field(default=500, ge=0)
    channel_limit: int = Field(default=500, ge=0)  # Backward-compatible alias.
    dry_run_mode: bool = False


class LiveM3uSettingsUpdate(BaseModel):
    output_file_path: str | None = Field(default=None, min_length=1)
    runtime_client_access_url: str | None = None
    include_disabled_channels: bool | None = None
    include_logos: bool | None = None
    include_group_title: bool | None = None
    include_tvg_id: bool | None = None
    include_tvg_name: bool | None = None
    generation_mode: GenerationMode | None = None
    maximum_live_channels: int | None = Field(default=None, ge=0)
    channel_limit: int | None = Field(default=None, ge=0)
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
    eligible_live_channels: int = 0
    configured_limit: int | None = 500
    included_channels: int = 0
    excluded_by_limit: int = 0
    capped: bool = True
    generation_mode: GenerationMode = "Test"
    percentage_complete: int = 0


class LiveM3uGenerateRequest(BaseModel):
    confirm_unlimited: bool = False


class LiveM3uEstimate(BaseModel):
    total_live_channels: int
    eligible_live_channels: int
    configured_limit: int | None
    included_channels: int
    excluded_by_limit: int
    capped: bool


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
