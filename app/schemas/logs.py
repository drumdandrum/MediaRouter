from datetime import datetime

from pydantic import BaseModel


class LogEntry(BaseModel):
    id: str
    level: str
    category: str
    message: str
    created_at: datetime


class LogCreate(BaseModel):
    level: str = "info"
    category: str = "system"
    message: str
