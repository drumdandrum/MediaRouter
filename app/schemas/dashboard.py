from pydantic import BaseModel


class DashboardStatus(BaseModel):
    app_name: str
    phase: str
    health: str
    health_label: str
    setup_status: str
    setup_status_label: str
    services_status: str
    services_status_label: str
    database_status: str
    database_status_label: str
    setup_complete: bool
    wizard_progress: int
    settings_count: int
    jobs_total: int
    jobs_running: int
    logs_total: int
    catalog_channels: int
    catalog_movies: int
    catalog_series: int
    catalog_episodes: int
    catalog_sources: int
    sprint_scope: list[str]
    deferred_scope: list[str]
