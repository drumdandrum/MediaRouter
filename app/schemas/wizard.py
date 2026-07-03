from pydantic import BaseModel, Field


class WizardStep(BaseModel):
    id: str
    label: str
    description: str


class WizardState(BaseModel):
    current_step: str = "foundation"
    completed_steps: list[str] = Field(default_factory=list)
    setup_complete: bool = False


class WizardStateUpdate(BaseModel):
    current_step: str | None = None
    completed_steps: list[str] | None = None
    setup_complete: bool | None = None
