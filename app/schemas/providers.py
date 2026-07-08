from datetime import datetime
from typing import Literal

from pydantic import BaseModel


ProviderType = Literal["IPTV", "HDHomeRun", "NextPVR", "Local Files", "Emby", "Jellyfin", "Other"]
HealthStatus = Literal["Unknown", "Healthy", "Degraded", "Authentication Failed", "Playlist Failed", "Offline", "Disabled"]
PriorityGroup = Literal["Preferred", "Secondary", "Emergency"]


class ProviderBase(BaseModel):
    friendly_name: str
    provider_type: ProviderType = "IPTV"
    notes: str = ""
    enabled: bool = True
    health_status: HealthStatus = "Unknown"


class ProviderCreate(ProviderBase):
    pass


class ProviderUpdate(BaseModel):
    friendly_name: str | None = None
    provider_type: ProviderType | None = None
    notes: str | None = None
    enabled: bool | None = None
    health_status: HealthStatus | None = None


class ProviderRead(ProviderBase):
    id: str
    created_at: datetime
    updated_at: datetime


class AccountBase(BaseModel):
    provider_id: str
    friendly_name: str
    username: str = ""
    base_url: str = ""
    max_simultaneous_streams: int = 1
    priority_group: PriorityGroup = "Preferred"
    weight: int = 100
    enabled: bool = True
    health_status: HealthStatus = "Unknown"
    notes: str = ""


class AccountCreate(AccountBase):
    password: str = ""


class AccountUpdate(BaseModel):
    provider_id: str | None = None
    friendly_name: str | None = None
    username: str | None = None
    password: str | None = None
    base_url: str | None = None
    max_simultaneous_streams: int | None = None
    priority_group: PriorityGroup | None = None
    weight: int | None = None
    enabled: bool | None = None
    health_status: HealthStatus | None = None
    notes: str | None = None


class AccountRead(AccountBase):
    id: str
    has_secret: bool
    last_success: datetime | None = None
    last_failure: datetime | None = None
    provider_name: str | None = None
    provider_type: str | None = None
    created_at: datetime
    updated_at: datetime


class ConnectionTestResult(BaseModel):
    account_id: str
    health_status: HealthStatus
    message: str
