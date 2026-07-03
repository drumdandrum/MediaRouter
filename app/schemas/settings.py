from pydantic import BaseModel, Field


class AppSettings(BaseModel):
    app_name: str = "Media Router"
    public_base_url: str = "http://media-router:8088"
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
