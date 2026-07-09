from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


ReservationStatus = Literal["active", "released", "expired", "failed"]


class BrokerResolveRequest(BaseModel):
    catalog_item_id: str
    media_type: str | None = None
    client_label: str | None = None
    reservation_ttl_seconds: int | None = Field(default=None, ge=1, le=86400)


class BrokerReleaseRequest(BaseModel):
    reservation_id: str


class BrokerSourceSelection(BaseModel):
    source_availability_id: int
    catalog_item_id: str
    catalog_title: str | None = None
    media_type: str
    location_ref: str
    provider_id: str
    provider_name: str
    provider_type: str
    account_id: str
    account_name: str
    priority_group: str
    weight: int
    active_reservations: int
    max_simultaneous_streams: int


class BrokerReservation(BaseModel):
    reservation_id: str
    catalog_item_id: str
    catalog_title: str | None = None
    source_availability_id: int
    provider_id: str
    provider_name: str | None = None
    account_id: str
    account_name: str | None = None
    media_type: str
    location_ref: str
    status: ReservationStatus
    created_at: datetime
    expires_at: datetime
    released_at: datetime | None = None
    client_label: str | None = None


class BrokerDecision(BaseModel):
    selected_source: BrokerSourceSelection
    reservation: BrokerReservation
    stream_url: str
    expires_at: datetime
    decision_reasons: list[str]


class BrokerErrorDetail(BaseModel):
    code: str
    message: str
    decision_reasons: list[str]


class BrokerAccountUsage(BaseModel):
    account_id: str
    account_name: str
    provider_id: str
    provider_name: str
    enabled: bool
    health_status: str
    priority_group: str
    weight: int
    active_reservations: int
    max_simultaneous_streams: int
    at_capacity: bool
    available: bool


class BrokerStatus(BaseModel):
    active_reservations: int
    released_reservations: int
    expired_reservations: int
    failed_reservations: int
    accounts_at_capacity: int
    available_accounts: int
    account_usage: list[BrokerAccountUsage]
