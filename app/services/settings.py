from app.core.config import get_settings
from app.core.json_store import JsonStore
from app.schemas.settings import AppSettings, SettingsUpdate


def _store() -> JsonStore:
    settings = get_settings()
    defaults = AppSettings(
        app_name=settings.app_name,
        public_base_url=settings.public_base_url,
        data_directory=str(settings.data_dir),
    ).model_dump()
    return JsonStore(settings.data_dir / "settings.json", defaults)


def get_app_settings() -> AppSettings:
    return AppSettings(**_store().read())


def update_app_settings(payload: SettingsUpdate) -> AppSettings:
    values = payload.model_dump(exclude_unset=True)
    return AppSettings(**_store().update(values))


def settings_categories() -> dict[str, list[str]]:
    return {
        "General": ["app_name", "timezone", "log_level"],
        "Storage": [
            "data_directory",
            "host_data_path",
            "container_data_path",
            "host_media_root",
            "container_media_root",
            "host_exports_path",
            "container_exports_path",
        ],
        "Network": ["public_base_url", "api_host", "api_port"],
        "Runtime": ["runtime_public_base_url", "trust_proxy_headers", "trusted_proxy_client_header",
                    "trusted_proxy_networks", "startup_coalescing_window_seconds"],
        "Services": ["emby_url", "jellyfin_url", "nextpvr_url", "iptv_boss_export_path"],
        "Advanced": ["environment_mode", "debug_enabled", "job_history_limit", "setup_complete"],
    }
