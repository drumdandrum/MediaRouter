from app.core.config import get_settings
from app.core.json_store import JsonStore
from app.schemas.wizard import WizardState, WizardStateUpdate, WizardStep


WIZARD_STEPS = [
    WizardStep(
        id="foundation",
        label="Foundation",
        description="Confirm Docker, FastAPI, docs, principles, and module boundaries.",
    ),
    WizardStep(
        id="settings",
        label="Settings",
        description="Set app name, public URL, timezone, log level, and local data directory.",
    ),
    WizardStep(
        id="dashboard",
        label="Dashboard",
        description="Review system health, wizard progress, settings, and jobs.",
    ),
    WizardStep(
        id="jobs",
        label="Job System",
        description="Run a foundation check job and verify background job tracking.",
    ),
]


def _store() -> JsonStore:
    return JsonStore(get_settings().data_dir / "wizard_state.json", WizardState().model_dump())


def get_wizard_steps() -> list[WizardStep]:
    return WIZARD_STEPS


def get_wizard_state() -> WizardState:
    return WizardState(**_store().read())


def update_wizard_state(payload: WizardStateUpdate) -> WizardState:
    values = payload.model_dump(exclude_unset=True)
    return WizardState(**_store().update(values))


def complete_step(step_id: str) -> WizardState:
    state = get_wizard_state()
    completed = list(dict.fromkeys([*state.completed_steps, step_id]))
    step_ids = [step.id for step in WIZARD_STEPS]
    next_step = state.current_step
    if step_id in step_ids:
        index = step_ids.index(step_id)
        if index + 1 < len(step_ids):
            next_step = step_ids[index + 1]
    setup_complete = len(completed) == len(WIZARD_STEPS)
    return update_wizard_state(
        WizardStateUpdate(
            current_step=next_step,
            completed_steps=completed,
            setup_complete=setup_complete,
        )
    )
