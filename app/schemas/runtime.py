from datetime import datetime

from pydantic import BaseModel

from app.schemas.broker import BrokerDecision, BrokerEvaluatedCandidate, BrokerSourceSelection
from app.schemas.catalog import CatalogItem


class RuntimePreview(BaseModel):
    catalog_item: CatalogItem
    media_type: str
    runtime_url: str
    debug_url: str
    available_source_count: int


class RuntimeResolveDebug(BaseModel):
    catalog_item: CatalogItem
    selected_provider: dict[str, str | int | None]
    selected_account: dict[str, str | int | None]
    selected_source: BrokerSourceSelection
    reservation_id: str
    expires_at: datetime
    stream_url: str
    reservation_action: str
    reuse_reason: str | None = None
    broker_decision: BrokerDecision
    evaluated_candidates: list[BrokerEvaluatedCandidate]


class RuntimeErrorDetail(BaseModel):
    failure_code: str
    failure_message: str
    decision_reasons: list[str] = []
    evaluated_candidates: list[BrokerEvaluatedCandidate] = []
