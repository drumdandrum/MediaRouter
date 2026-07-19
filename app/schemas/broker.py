from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


ReservationStatus = Literal["provisional", "active", "released", "expired", "superseded", "failed"]


class BrokerResolveRequest(BaseModel):
    catalog_item_id: str
    media_type: str | None = None
    client_label: str | None = None
    client_session: str | None = None
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


class BrokerEvaluatedCandidate(BaseModel):
    source_availability_id: int | None = None
    catalog_item_id: str | None = None
    media_type: str | None = None
    provider_id: str | None = None
    provider_name: str | None = None
    account_id: str | None = None
    account_name: str | None = None
    priority_group: str | None = None
    weight: int | None = None
    active_reservations: int = 0
    max_simultaneous_streams: int | None = None
    source_enabled: bool = False
    provider_enabled: bool = False
    account_enabled: bool = False
    provider_health_status: str | None = None
    account_health_status: str | None = None
    selected: bool = False
    reason: str
    reason_detail: str


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
    client_session: str | None = None
    last_reused_at: datetime | None = None
    reuse_count: int = 0
    identity_type: str | None = None
    masked_client_identity: str | None = None
    last_seen_at: datetime | None = None
    last_action: str = "reservation_created"
    duplicate_warning: bool = False
    alias_count: int = 0
    coalesced_reuse_count: int = 0
    startup_coalesced: bool = False
    lifecycle_state: ReservationStatus
    provisional_expires_at: datetime | None = None
    active_expires_at: datetime | None = None
    promoted_at: datetime | None = None
    superseded_at: datetime | None = None
    superseded_by_reservation_id: str | None = None
    first_seen_at: datetime | None = None
    request_count: int = 1
    distinct_activity_count: int = 0
    promotion_reason: str | None = None
    release_reason: str | None = None
    last_confirmation_source: str | None = None
    last_confirmed_at: datetime | None = None


class DuplicateRepairResult(BaseModel):
    duplicate_groups: int = 0
    released_reservations: int = 0
    kept_reservation_ids: list[str] = []


class BrokerDecision(BaseModel):
    selected_source: BrokerSourceSelection | None = None
    reservation: BrokerReservation | None = None
    stream_url: str | None = None
    expires_at: datetime | None = None
    reservation_ttl_seconds: int
    decision_reason: str
    decision_reasons: list[str]
    evaluated_candidates: list[BrokerEvaluatedCandidate]
    failure_code: str | None = None
    failure_message: str | None = None
    reservation_created: bool = False
    reservation_reused: bool = False
    reuse_reason: str | None = None
    runtime_url: str | None = None


class BrokerErrorDetail(BaseModel):
    code: str
    message: str
    failure_code: str
    failure_message: str
    decision_reasons: list[str]
    evaluated_candidates: list[BrokerEvaluatedCandidate] = []


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
    provisional_reservations: int = 0
    consuming_reservations: int = 0
    max_simultaneous_streams: int
    at_capacity: bool
    available: bool


class BrokerStatus(BaseModel):
    total_reservations: int
    active_reservations: int
    provisional_reservations: int = 0
    superseded_reservations: int = 0
    consuming_reservations: int = 0
    released_reservations: int
    expired_reservations: int
    failed_reservations: int
    accounts_at_capacity: int
    available_accounts: int
    account_usage: list[BrokerAccountUsage]
