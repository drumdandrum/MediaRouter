from fastapi import APIRouter, HTTPException, Query

from app.schemas.catalog import SourceAvailability, SourceAvailabilityUpdate
from app.services.catalog import delete_source_availability, list_source_availability, update_source_availability

router = APIRouter(prefix="/api/sources", tags=["sources"])


@router.get("", response_model=list[SourceAvailability])
def read_sources(limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)) -> list[SourceAvailability]:
    return list_source_availability(limit=limit, offset=offset)


@router.put("/{source_id}", response_model=SourceAvailability)
def edit_source(source_id: int, payload: SourceAvailabilityUpdate) -> SourceAvailability:
    source = update_source_availability(source_id, payload)
    if source is None:
        raise HTTPException(status_code=404, detail="Source availability record not found")
    return source


@router.delete("/{source_id}", status_code=204)
def remove_source(source_id: int) -> None:
    if not delete_source_availability(source_id):
        raise HTTPException(status_code=404, detail="Source availability record not found")
