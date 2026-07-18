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
                    "trusted_proxy_networks", "startup_coalescing_window_seconds",
                    "live_provisional_ttl_seconds", "live_promotion_minimum_age_seconds",
                    "live_promotion_request_threshold", "live_active_ttl_seconds",
                    "live_same_identity_supersession", "movie_provisional_ttl_seconds",
                    "movie_promotion_minimum_age_seconds", "movie_promotion_request_threshold",
                    "movie_active_ttl_seconds", "movie_provisional_supersession",
                    "episode_provisional_ttl_seconds", "episode_promotion_minimum_age_seconds",
                    "episode_promotion_request_threshold", "episode_active_ttl_seconds",
                    "episode_provisional_supersession", "active_lease_sliding_renewal"],
        "Services": ["emby_url", "jellyfin_url", "nextpvr_url", "iptv_boss_export_path"],
        "Advanced": ["environment_mode", "debug_enabled", "job_history_limit", "setup_complete"],
    }
