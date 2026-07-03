from datetime import datetime

from pydantic import BaseModel, Field


class JobCreate(BaseModel):
    kind: str = Field(default="foundation_check")


class JobRead(BaseModel):
    id: str
    kind: str
    status: str
    progress: int
    message: str
    created_at: datetime
    updated_at: datetime
