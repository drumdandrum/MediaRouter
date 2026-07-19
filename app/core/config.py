from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Media Router"
    app_host: str = "0.0.0.0"
    app_port: int = 8088
    public_base_url: str = "http://media-router:8088"
    data_dir: Path = Path("./data")
    playback_ticket_secret: str = ""
    playback_ticket_ttl_seconds: int = 60

    model_config = SettingsConfigDict(env_file=".env", env_prefix="MEDIA_ROUTER_")


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return settings
