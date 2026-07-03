from dataclasses import dataclass

from app.domain.types import MediaKind, ServiceStatus


@dataclass(frozen=True)
class ModuleDescriptor:
    name: str
    label: str
    responsibility: str
    status: ServiceStatus = ServiceStatus.NOT_CONFIGURED


@dataclass(frozen=True)
class Principle:
    number: int
    title: str
    rule: str


@dataclass(frozen=True)
class BrokerRequest:
    media_kind: MediaKind
    internal_id: str
    client_hint: str | None = None


@dataclass(frozen=True)
class BrokerDecision:
    account_id: str
    source_url: str
    reservation_id: str
