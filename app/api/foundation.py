from fastapi import APIRouter

from app.domain.contracts import ModuleDescriptor, Principle
from app.domain.types import ServiceStatus

router = APIRouter(prefix="/api", tags=["foundation"])


MODULES = [
    ModuleDescriptor(
        name="wizard",
        label="First Run Wizard",
        responsibility="Guided setup, environment discovery, and initial configuration.",
        status=ServiceStatus.READY,
    ),
    ModuleDescriptor(
        name="accounts",
        label="Providers And Accounts",
        responsibility="Provider-agnostic origins, connection credentials, availability, limits, health, and priority metadata.",
        status=ServiceStatus.READY,
    ),
    ModuleDescriptor(
        name="catalog",
        label="Catalog",
        responsibility="Permanent internal IDs for movies, episodes, and live channels.",
        status=ServiceStatus.READY,
    ),
    ModuleDescriptor(
        name="broker",
        label="Stream Broker",
        responsibility="Decision-only source selection, account capacity checks, and temporary reservations.",
        status=ServiceStatus.READY,
    ),
    ModuleDescriptor(
        name="outputs",
        label="Outputs",
        responsibility="Plugin boundary for STRM, M3U, XMLTV, HDHomeRun, REST, and future outputs.",
        status=ServiceStatus.READY,
    ),
    ModuleDescriptor(
        name="integrations",
        label="Integrations",
        responsibility="Emby, Jellyfin, NextPVR, Channels DVR, IPTV Boss, and future service adapters.",
    ),
    ModuleDescriptor(
        name="settings",
        label="Settings",
        responsibility="UI-managed configuration, path mapping, and secrets boundary.",
        status=ServiceStatus.READY,
    ),
    ModuleDescriptor(
        name="dashboard",
        label="Dashboard",
        responsibility="Sprint status, setup progress, settings summary, and job activity.",
        status=ServiceStatus.READY,
    ),
    ModuleDescriptor(
        name="jobs",
        label="Job System",
        responsibility="Background task tracking for foundation checks and future long-running work.",
        status=ServiceStatus.READY,
    ),
]

PRINCIPLES = [
    Principle(
        number=1,
        title="IPTV Boss is the editorial source",
        rule="Media Router reads IPTV Boss exports but never edits IPTV Boss data.",
    ),
    Principle(
        number=2,
        title="Media Router owns the runtime catalog",
        rule="Runtime identity, source mappings, broker decisions, and generated outputs are based on Media Router catalog records.",
    ),
    Principle(
        number=3,
        title="Outputs are disposable",
        rule="Generated STRM, M3U, XMLTV, HDHomeRun, and future outputs are rebuildable artifacts, not authoritative state.",
    ),
    Principle(
        number=4,
        title="Plugins communicate only through the service layer",
        rule="Plugins never directly access SQLite or module-owned persistence internals.",
    ),
    Principle(
        number=5,
        title="Every catalog item has exactly one internal ID",
        rule="Each movie, episode, and live channel has one stable internal ID; provider sources map to that ID.",
    ),
]


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": ServiceStatus.READY.value}


@router.get("/foundation")
def foundation() -> dict:
    return {
        "project": "Media Router",
        "phase": "sprint_7_live_tv_m3u_output",
        "implementation_scope": "catalog, provider/account availability, broker decisions, runtime URLs, STRM output, and Live TV M3U output only: no playback, proxy streaming, transcoding, XMLTV, HDHomeRun, or media-server integrations",
        "principles": [principle.__dict__ for principle in PRINCIPLES],
        "modules": [module.__dict__ for module in MODULES],
    }
