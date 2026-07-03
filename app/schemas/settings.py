from pydantic import BaseModel, Field


class AppSettings(BaseModel):
    app_name: str = "Media Router"
    public_base_url: str = "http://media-router:8088"
    timezone: str = "America/Los_Angeles"
    log_level: str = "info"
    data_directory: str = "./data"
    setup_complete: bool = False


class SettingsUpdate(BaseModel):
    app_name: str | None = Field(default=None, min_length=1)
    public_base_url: str | None = None
    timezone: str | None = None
    log_level: str | None = None
    data_directory: str | None = None
    setup_complete: bool | None = None
