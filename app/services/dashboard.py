from app.schemas.dashboard import DashboardStatus
from app.services.broker import get_status as get_broker_status
from app.services.catalog import get_summary, source_availability_summary
from app.services.jobs import job_counts
from app.services.logs import log_count
from app.services.providers import provider_account_summary
from app.services.settings import get_app_settings
from app.services.system import get_system_info
from app.services.wizard import get_wizard_state, get_wizard_steps


SPRINT_SCOPE = ["Foundation", "Wizard", "Settings", "Dashboard", "Job System", "Logs", "About/System", "Catalog Engine", "Providers", "Accounts", "Source Availability", "Broker Decision Engine"]
DEFERRED_SCOPE = ["Outputs", "Integrations", "Playback", "Proxy Streaming", "Transcoding"]


def get_dashboard_status() -> DashboardStatus:
    settings = get_app_settings()
    wizard = get_wizard_state()
    steps = get_wizard_steps()
    total_jobs, running_jobs = job_counts()
    system = get_system_info()
    catalog = get_summary()
    providers = provider_account_summary()
    availability = source_availability_summary()
    broker = get_broker_status()
    services_configured = any([settings.emby_url, settings.jellyfin_url, settings.nextpvr_url, settings.iptv_boss_export_path])
    return DashboardStatus(
        app_name=settings.app_name,
        phase="Sprint 4",
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
        catalog_channels=catalog.channels,
        catalog_movies=catalog.movies,
        catalog_series=catalog.series,
        catalog_episodes=catalog.episodes,
        catalog_sources=catalog.sources,
        providers_configured=providers["providers"],
        accounts_configured=providers["accounts"],
        healthy_accounts=providers["healthy_accounts"],
        disabled_accounts=providers["disabled_accounts"],
        problem_accounts=providers["problem_accounts"],
        source_availability_records=availability["source_availability"],
        average_sources_per_item=availability["average_sources_per_item"],
        broker_active_reservations=broker.active_reservations,
        broker_expired_reservations=broker.expired_reservations,
        broker_accounts_at_capacity=broker.accounts_at_capacity,
        broker_available_accounts=broker.available_accounts,
        sprint_scope=SPRINT_SCOPE,
        deferred_scope=DEFERRED_SCOPE,
    )
