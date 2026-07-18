from fastapi import APIRouter, HTTPException

from app.schemas.broker import BrokerDecision, BrokerReleaseRequest, BrokerReservation, BrokerResolveRequest, BrokerStatus, DuplicateRepairResult
from app.services.broker import BrokerUnavailable, confirm_reservation, expire_now, force_expire_reservation, get_status, heartbeat_reservation, list_reservations, release_all_active, release_reservation, repair_duplicate_reservations, resolve_source

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


def _reservation_or_404(reservation: BrokerReservation | None) -> BrokerReservation:
    if reservation is None:
        raise HTTPException(status_code=404, detail="Reservation not found")
    return reservation


@router.post("/reservations/{reservation_id}/confirm", response_model=BrokerReservation)
def broker_confirm(reservation_id: str) -> BrokerReservation:
    try:
        return _reservation_or_404(confirm_reservation(reservation_id))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/reservations/{reservation_id}/heartbeat", response_model=BrokerReservation)
def broker_heartbeat(reservation_id: str) -> BrokerReservation:
    return _reservation_or_404(heartbeat_reservation(reservation_id))


@router.post("/reservations/{reservation_id}/release", response_model=BrokerReservation)
def broker_release_by_id(reservation_id: str) -> BrokerReservation:
    return _reservation_or_404(release_reservation(reservation_id))


@router.post("/reservations/{reservation_id}/expire", response_model=BrokerReservation)
def broker_expire_by_id(reservation_id: str) -> BrokerReservation:
    return _reservation_or_404(force_expire_reservation(reservation_id))


@router.post("/release-all", response_model=BrokerStatus)
def broker_release_all() -> BrokerStatus:
    return release_all_active()


@router.post("/expire-now", response_model=BrokerStatus)
def broker_expire_now() -> BrokerStatus:
    return expire_now()


@router.post("/repair-duplicates", response_model=DuplicateRepairResult)
def broker_repair_duplicates() -> DuplicateRepairResult:
    return repair_duplicate_reservations()
