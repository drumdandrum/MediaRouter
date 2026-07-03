from fastapi import APIRouter

from app.schemas.system import SystemInfo
from app.services.system import get_system_info

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("", response_model=SystemInfo)
def read_system() -> SystemInfo:
    return get_system_info()
