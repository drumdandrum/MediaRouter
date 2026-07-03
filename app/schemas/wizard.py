from pydantic import BaseModel, Field


class WizardStep(BaseModel):
    id: str
    label: str
    description: str
    fields: list[str] = Field(default_factory=list)


class WizardState(BaseModel):
    current_step: str = "welcome"
    completed_steps: list[str] = Field(default_factory=list)
    values: dict[str, str] = Field(default_factory=dict)
    setup_complete: bool = False


class WizardStateUpdate(BaseModel):
    current_step: str | None = None
    completed_steps: list[str] | None = None
    values: dict[str, str] | None = None
    setup_complete: bool | None = None
