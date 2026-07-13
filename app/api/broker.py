from fastapi import APIRouter, HTTPException

from app.schemas.broker import BrokerDecision, BrokerReleaseRequest, BrokerReservation, BrokerResolveRequest, BrokerStatus, DuplicateRepairResult
from app.services.broker import BrokerUnavailable, expire_now, get_status, list_reservations, release_all_active, release_reservation, repair_duplicate_reservations, resolve_source

router = APIRouter(prefix="/api/broker", tags=["broker"])


@router.get("/status", response_model=BrokerStatus)
def broker_status() -> BrokerStatus:
    return get_status()


@router.get("/reservations", response_model=list[BrokerReservation])
def broker_reservations() -> list[BrokerReservation]:
    return list_reservations()


@router.post("/resolve", response_model=BrokerDecision, status_code=201)
def broker_resolve(payload: BrokerResolveRequest) -> BrokerDecision:
    try:
        return resolve_source(
            catalog_item_id=payload.catalog_item_id,
            media_type=payload.media_type,
            client_label=payload.client_label,
            client_session=payload.client_session,
            reservation_ttl_seconds=payload.reservation_ttl_seconds,
        )
    except BrokerUnavailable as exc:
        raise HTTPException(status_code=409, detail=exc.detail.model_dump()) from exc


@router.post("/release", response_model=BrokerReservation)
def broker_release(payload: BrokerReleaseRequest) -> BrokerReservation:
    reservation = release_reservation(payload.reservation_id)
    if reservation is None:
        raise HTTPException(status_code=404, detail="Reservation not found")
    return reservation


@router.post("/release-all", response_model=BrokerStatus)
def broker_release_all() -> BrokerStatus:
    return release_all_active()


@router.post("/expire-now", response_model=BrokerStatus)
def broker_expire_now() -> BrokerStatus:
    return expire_now()


@router.post("/repair-duplicates", response_model=DuplicateRepairResult)
def broker_repair_duplicates() -> DuplicateRepairResult:
    return repair_duplicate_reservations()
