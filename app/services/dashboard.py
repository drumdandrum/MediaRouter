from app.schemas.dashboard import DashboardStatus
from app.services.jobs import job_counts
from app.services.settings import get_app_settings
from app.services.wizard import get_wizard_state, get_wizard_steps


SPRINT_SCOPE = ["Foundation", "Wizard", "Settings", "Dashboard", "Job System"]
DEFERRED_SCOPE = ["Catalog", "Broker", "Accounts", "Outputs", "Integrations"]


def get_dashboard_status() -> DashboardStatus:
    settings = get_app_settings()
    wizard = get_wizard_state()
    steps = get_wizard_steps()
    total_jobs, running_jobs = job_counts()
    return DashboardStatus(
        app_name=settings.app_name,
        phase="Sprint 1",
        health="ready",
        setup_complete=wizard.setup_complete,
        wizard_progress=round((len(wizard.completed_steps) / len(steps)) * 100) if steps else 0,
        settings_count=len(settings.model_dump()),
        jobs_total=total_jobs,
        jobs_running=running_jobs,
        sprint_scope=SPRINT_SCOPE,
        deferred_scope=DEFERRED_SCOPE,
    )
