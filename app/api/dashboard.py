from fastapi import APIRouter

from app.schemas.dashboard import DashboardStatus
from app.services.dashboard import get_dashboard_status

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("", response_model=DashboardStatus)
def dashboard() -> DashboardStatus:
    return get_dashboard_status()
