from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os

from app.core.config import get_settings


class PlaybackTicketError(ValueError):
    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def _secret() -> bytes:
    settings = get_settings()
    if settings.playback_ticket_secret.strip():
        return settings.playback_ticket_secret.encode("utf-8")
    path = settings.data_dir / ".playback_ticket_secret"
    try:
        secret = path.read_bytes()
    except FileNotFoundError:
        secret = os.urandom(32)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(secret)
        except FileExistsError:
            secret = path.read_bytes()
    if len(secret) < 32:
        raise RuntimeError("Playback ticket secret must contain at least 32 bytes")
    return secret


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def issue_playback_ticket(reservation_id: str, catalog_item_id: str, expires_at: datetime) -> tuple[str, datetime]:
    now = datetime.now(timezone.utc)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    ticket_expiry = min(expires_at, now + timedelta(seconds=get_settings().playback_ticket_ttl_seconds))
    payload = {"v": 1, "r": reservation_id, "c": catalog_item_id, "exp": int(ticket_expiry.timestamp())}
    encoded = _encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    signature = _encode(hmac.new(_secret(), encoded.encode("ascii"), hashlib.sha256).digest())
    return f"{encoded}.{signature}", ticket_expiry


def validate_playback_ticket(ticket: str) -> dict[str, str | int]:
    try:
        encoded, supplied = ticket.split(".", 1)
        expected = _encode(hmac.new(_secret(), encoded.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(supplied, expected):
            raise PlaybackTicketError("invalid_ticket", "Playback ticket signature is invalid.")
        payload = json.loads(_decode(encoded))
        if payload.get("v") != 1 or not isinstance(payload.get("r"), str) or not isinstance(payload.get("c"), str) or not isinstance(payload.get("exp"), int):
            raise PlaybackTicketError("invalid_ticket", "Playback ticket payload is invalid.")
    except PlaybackTicketError:
        raise
    except Exception as exc:
        raise PlaybackTicketError("invalid_ticket", "Playback ticket is malformed.") from exc
    if int(payload["exp"]) < int(datetime.now(timezone.utc).timestamp()):
        raise PlaybackTicketError("expired_ticket", "Playback ticket has expired.", 410)
    return payload
