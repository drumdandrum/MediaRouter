from app.schemas.dashboard import DashboardStatus
from app.services.jobs import job_counts
from app.services.logs import log_count
from app.services.settings import get_app_settings
from app.services.system import get_system_info
from app.services.wizard import get_wizard_state, get_wizard_steps


SPRINT_SCOPE = ["Foundation", "Wizard", "Settings", "Dashboard", "Job System"]
DEFERRED_SCOPE = ["Catalog", "Broker", "Accounts", "Outputs", "Integrations"]


def get_dashboard_status() -> DashboardStatus:
    settings = get_app_settings()
    wizard = get_wizard_state()
    steps = get_wizard_steps()
    total_jobs, running_jobs = job_counts()
    system = get_system_info()
    services_configured = any([settings.emby_url, settings.jellyfin_url, settings.nextpvr_url, settings.iptv_boss_export_path])
    return DashboardStatus(
        app_name=settings.app_name,
        phase="Sprint 1.5",
        health="ready",
        health_label="Ready",
        setup_status="ready" if wizard.setup_complete else "needs_setup",
        setup_status_label="Ready" if wizard.setup_complete else "Needs setup",
        services_status="ready" if services_configured else "not_configured",
        services_status_label="Ready" if services_configured else "Not configured",
        database_status=system.database_status,
        database_status_label=system.database_label,
        setup_complete=wizard.setup_complete,
        wizard_progress=round((len(wizard.completed_steps) / len(steps)) * 100) if steps else 0,
        settings_count=len(settings.model_dump()),
        jobs_total=total_jobs,
        jobs_running=running_jobs,
        logs_total=log_count(),
        sprint_scope=["Foundation", "Wizard", "Settings", "Dashboard", "Job System", "Logs", "About/System"],
        deferred_scope=DEFERRED_SCOPE,
    )
