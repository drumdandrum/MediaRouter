from pydantic import BaseModel


class DashboardStatus(BaseModel):
    app_name: str
    phase: str
    health: str
    setup_complete: bool
    wizard_progress: int
    settings_count: int
    jobs_total: int
    jobs_running: int
    sprint_scope: list[str]
    deferred_scope: list[str]
