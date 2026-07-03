from fastapi import APIRouter

from app.schemas.settings import AppSettings, SettingsUpdate
from app.services.settings import get_app_settings, settings_categories, update_app_settings

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("", response_model=AppSettings)
def read_settings() -> AppSettings:
    return get_app_settings()


@router.get("/categories")
def read_categories() -> dict[str, list[str]]:
    return settings_categories()


@router.put("", response_model=AppSettings)
def write_settings(payload: SettingsUpdate) -> AppSettings:
    return update_app_settings(payload)
