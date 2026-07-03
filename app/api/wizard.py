from fastapi import APIRouter

from app.schemas.wizard import WizardState, WizardStateUpdate, WizardStep
from app.services.wizard import complete_step, get_wizard_state, get_wizard_steps, merge_wizard_values, update_wizard_state

router = APIRouter(prefix="/api/wizard", tags=["wizard"])


@router.get("/steps", response_model=list[WizardStep])
def read_steps() -> list[WizardStep]:
    return get_wizard_steps()


@router.get("/state", response_model=WizardState)
def read_state() -> WizardState:
    return get_wizard_state()


@router.put("/state", response_model=WizardState)
def write_state(payload: WizardStateUpdate) -> WizardState:
    return update_wizard_state(payload)


@router.put("/values", response_model=WizardState)
def write_values(values: dict[str, str]) -> WizardState:
    return merge_wizard_values(values)


@router.post("/steps/{step_id}/complete", response_model=WizardState)
def complete(step_id: str) -> WizardState:
    return complete_step(step_id)
