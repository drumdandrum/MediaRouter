from pydantic import BaseModel, Field


class AppSettings(BaseModel):
    app_name: str = "Media Router"
    public_base_url: str = "http://media-router:8088"
    runtime_public_base_url: str = ""
    trust_proxy_headers: bool = False
    trusted_proxy_client_header: str = "x-forwarded-for"
    trusted_proxy_networks: str = ""
    startup_coalescing_window_seconds: int = Field(default=90, ge=0, le=600)
    live_provisional_ttl_seconds: int = Field(default=45, ge=10, le=300)
    live_promotion_minimum_age_seconds: int = Field(default=20, ge=5, le=180)
    live_promotion_request_threshold: int = Field(default=2, ge=1, le=10)
    live_active_ttl_seconds: int = Field(default=14400, ge=300, le=86400)
    live_same_identity_supersession: bool = True
    movie_provisional_ttl_seconds: int = Field(default=60, ge=10, le=300)
    movie_promotion_minimum_age_seconds: int = Field(default=20, ge=5, le=180)
    movie_promotion_request_threshold: int = Field(default=2, ge=1, le=10)
    movie_active_ttl_seconds: int = Field(default=10800, ge=300, le=86400)
    movie_provisional_supersession: bool = True
    episode_provisional_ttl_seconds: int = Field(default=60, ge=10, le=300)
    episode_promotion_minimum_age_seconds: int = Field(default=20, ge=5, le=180)
    episode_promotion_request_threshold: int = Field(default=2, ge=1, le=10)
    episode_active_ttl_seconds: int = Field(default=7200, ge=300, le=86400)
    episode_provisional_supersession: bool = True
    active_lease_sliding_renewal: bool = True
    timezone: str = "America/Los_Angeles"
    log_level: str = "info"
    data_directory: str = "./data"
    host_data_path: str = "./data"
    container_data_path: str = "/data"
    host_media_root: str = ""
    container_media_root: str = "/media"
    host_exports_path: str = ""
    container_exports_path: str = "/exports"
    environment_mode: str = "local"
    api_host: str = "0.0.0.0"
    api_port: int = 8088
    emby_url: str = ""
    jellyfin_url: str = ""
    nextpvr_url: str = ""
    iptv_boss_export_path: str = ""
    debug_enabled: bool = False
    job_history_limit: int = 50
    setup_complete: bool = False


class SettingsUpdate(BaseModel):
    app_name: str | None = Field(default=None, min_length=1)
    public_base_url: str | None = None
    runtime_public_base_url: str | None = None
    trust_proxy_headers: bool | None = None
    trusted_proxy_client_header: str | None = None
    trusted_proxy_networks: str | None = None
    startup_coalescing_window_seconds: int | None = Field(default=None, ge=0, le=600)
    live_provisional_ttl_seconds: int | None = Field(default=None, ge=10, le=300)
    live_promotion_minimum_age_seconds: int | None = Field(default=None, ge=5, le=180)
    live_promotion_request_threshold: int | None = Field(default=None, ge=1, le=10)
    live_active_ttl_seconds: int | None = Field(default=None, ge=300, le=86400)
    live_same_identity_supersession: bool | None = None
    movie_provisional_ttl_seconds: int | None = Field(default=None, ge=10, le=300)
    movie_promotion_minimum_age_seconds: int | None = Field(default=None, ge=5, le=180)
    movie_promotion_request_threshold: int | None = Field(default=None, ge=1, le=10)
    movie_active_ttl_seconds: int | None = Field(default=None, ge=300, le=86400)
    movie_provisional_supersession: bool | None = None
    episode_provisional_ttl_seconds: int | None = Field(default=None, ge=10, le=300)
    episode_promotion_minimum_age_seconds: int | None = Field(default=None, ge=5, le=180)
    episode_promotion_request_threshold: int | None = Field(default=None, ge=1, le=10)
    episode_active_ttl_seconds: int | None = Field(default=None, ge=300, le=86400)
    episode_provisional_supersession: bool | None = None
    active_lease_sliding_renewal: bool | None = None
    timezone: str | None = None
    log_level: str | None = None
    data_directory: str | None = None
    host_data_path: str | None = None
    container_data_path: str | None = None
    host_media_root: str | None = None
    container_media_root: str | None = None
    host_exports_path: str | None = None
    container_exports_path: str | None = None
    environment_mode: str | None = None
    api_host: str | None = None
    api_port: int | None = None
    emby_url: str | None = None
    jellyfin_url: str | None = None
    nextpvr_url: str | None = None
    iptv_boss_export_path: str | None = None
    debug_enabled: bool | None = None
    job_history_limit: int | None = None
    setup_complete: bool | None = None
