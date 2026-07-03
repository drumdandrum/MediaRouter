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
