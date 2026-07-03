from app.core.config import get_settings
from app.core.json_store import JsonStore
from app.schemas.wizard import WizardState, WizardStateUpdate, WizardStep


WIZARD_STEPS = [
    WizardStep(
        id="welcome",
        label="Welcome",
        description="Confirm this is a foundation-only setup. Catalog, broker, and media integrations are deferred.",
    ),
    WizardStep(
        id="environment",
        label="Environment",
        description="Record the current environment mode. Detection is placeholder-only in Sprint 1.5.",
        fields=["environment_mode"],
    ),
    WizardStep(
        id="paths",
        label="Paths",
        description="Enter host and container paths manually until real detection lands in a later sprint.",
        fields=["host_data_path", "container_data_path", "host_media_root", "container_media_root"],
    ),
    WizardStep(
        id="services",
        label="Services",
        description="Enter optional service URLs. These are placeholders and no connections are attempted yet.",
        fields=["emby_url", "jellyfin_url", "nextpvr_url", "iptv_boss_export_path"],
    ),
    WizardStep(
        id="review",
        label="Review",
        description="Review settings and confirm the foundation is ready.",
    ),
    WizardStep(
        id="complete",
        label="Complete",
        description="Persist setup completion state.",
    ),
]


def _store() -> JsonStore:
    return JsonStore(get_settings().data_dir / "wizard_state.json", WizardState().model_dump())


def get_wizard_steps() -> list[WizardStep]:
    return WIZARD_STEPS


def get_wizard_state() -> WizardState:
    state = WizardState(**_store().read())
    step_ids = [step.id for step in WIZARD_STEPS]
    completed = [step_id for step_id in state.completed_steps if step_id in step_ids]
    current = state.current_step if state.current_step in step_ids else next(
        (step_id for step_id in step_ids if step_id not in completed),
        step_ids[0],
    )
    setup_complete = len(completed) == len(step_ids)
    if completed != state.completed_steps or current != state.current_step or setup_complete != state.setup_complete:
        return update_wizard_state(
            WizardStateUpdate(
                current_step=current,
                completed_steps=completed,
                setup_complete=setup_complete,
            )
        )
    return state


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
    setup_complete = len(completed) == len(WIZARD_STEPS) or step_id == "complete"
    return update_wizard_state(
        WizardStateUpdate(
            current_step=next_step,
            completed_steps=completed,
            setup_complete=setup_complete,
        )
    )


def merge_wizard_values(values: dict[str, str]) -> WizardState:
    state = get_wizard_state()
    return update_wizard_state(WizardStateUpdate(values={**state.values, **values}))
