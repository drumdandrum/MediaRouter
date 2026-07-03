from enum import StrEnum


class MediaKind(StrEnum):
    MOVIE = "movie"
    SERIES = "series"
    LIVE = "live"


class ServiceStatus(StrEnum):
    NOT_CONFIGURED = "not_configured"
    READY = "ready"
    DEGRADED = "degraded"
    OFFLINE = "offline"
