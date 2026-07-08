from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sqlite3
from urllib.error import URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from app.core.config import get_settings
from app.schemas.providers import AccountCreate, AccountRead, AccountUpdate, ConnectionTestResult, ProviderCreate, ProviderRead, ProviderUpdate
from app.services.logs import add_log


HEALTH_STATUSES = {"Unknown", "Healthy", "Degraded", "Authentication Failed", "Playlist Failed", "Offline", "Disabled"}
PRIORITY_GROUPS = {"Preferred", "Secondary", "Emergency"}
PROVIDER_TYPES = {"IPTV", "HDHomeRun", "NextPVR", "Local Files", "Emby", "Jellyfin", "Other"}


def _db_path() -> Path:
    return get_settings().data_dir / "media_router.db"


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    ensure_provider_schema(conn)
    return conn


def ensure_provider_schema(conn: sqlite3.Connection | None = None) -> None:
    owns_conn = conn is None
    if conn is None:
        path = _db_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS providers (
            id TEXT PRIMARY KEY,
            friendly_name TEXT NOT NULL,
            provider_type TEXT NOT NULL,
            notes TEXT NOT NULL DEFAULT '',
            enabled INTEGER NOT NULL DEFAULT 1,
            health_status TEXT NOT NULL DEFAULT 'Unknown',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS accounts (
            id TEXT PRIMARY KEY,
            provider_id TEXT NOT NULL,
            friendly_name TEXT NOT NULL,
            username TEXT NOT NULL DEFAULT '',
            password_secret TEXT NOT NULL DEFAULT '',
            base_url TEXT NOT NULL DEFAULT '',
            playlist_url TEXT NOT NULL DEFAULT '',
            max_simultaneous_streams INTEGER NOT NULL DEFAULT 1,
            priority_group TEXT NOT NULL DEFAULT 'Preferred',
            weight INTEGER NOT NULL DEFAULT 100,
            enabled INTEGER NOT NULL DEFAULT 1,
            health_status TEXT NOT NULL DEFAULT 'Unknown',
            last_success TEXT,
            last_failure TEXT,
            notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(provider_id) REFERENCES providers(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_accounts_provider ON accounts(provider_id);
        """
    )
    conn.commit()
    if owns_conn:
        conn.close()


def _now() -> str:
    return datetime.utcnow().isoformat()


def _bool(value: int) -> bool:
    return bool(value)


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _provider_from_row(row: sqlite3.Row) -> ProviderRead:
    return ProviderRead(
        id=row["id"],
        friendly_name=row["friendly_name"],
        provider_type=row["provider_type"],
        notes=row["notes"],
        enabled=_bool(row["enabled"]),
        health_status=row["health_status"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _account_from_row(row: sqlite3.Row) -> AccountRead:
    return AccountRead(
        id=row["id"],
        provider_id=row["provider_id"],
        provider_name=row["provider_name"] if "provider_name" in row.keys() else None,
        provider_type=row["provider_type"] if "provider_type" in row.keys() else None,
        friendly_name=row["friendly_name"],
        username=row["username"],
        base_url=row["base_url"],
        max_simultaneous_streams=row["max_simultaneous_streams"],
        priority_group=row["priority_group"],
        weight=row["weight"],
        enabled=_bool(row["enabled"]),
        health_status=row["health_status"],
        has_secret=bool(row["password_secret"]),
        last_success=_dt(row["last_success"]),
        last_failure=_dt(row["last_failure"]),
        notes=row["notes"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def list_providers() -> list[ProviderRead]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM providers ORDER BY friendly_name").fetchall()
    return [_provider_from_row(row) for row in rows]


def get_provider(provider_id: str) -> ProviderRead | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM providers WHERE id = ?", (provider_id,)).fetchone()
    return _provider_from_row(row) if row else None


def create_provider(payload: ProviderCreate) -> ProviderRead:
    now = _now()
    provider_id = uuid4().hex
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO providers (id, friendly_name, provider_type, notes, enabled, health_status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (provider_id, payload.friendly_name, payload.provider_type, payload.notes, int(payload.enabled), payload.health_status, now, now),
        )
        conn.commit()
    add_log("info", "providers", f"Created provider {payload.friendly_name}")
    return get_provider(provider_id)


def update_provider(provider_id: str, payload: ProviderUpdate) -> ProviderRead | None:
    existing = get_provider(provider_id)
    if existing is None:
        return None
    data = payload.model_dump(exclude_unset=True)
    if not data:
        return existing
    data["updated_at"] = _now()
    if "enabled" in data:
        data["enabled"] = int(data["enabled"])
        if not data["enabled"]:
            data["health_status"] = "Disabled"
    assignments = ", ".join(f"{key} = :{key}" for key in data)
    data["id"] = provider_id
    with _connect() as conn:
        conn.execute(f"UPDATE providers SET {assignments} WHERE id = :id", data)
        conn.commit()
    return get_provider(provider_id)


def delete_provider(provider_id: str) -> bool:
    with _connect() as conn:
        result = conn.execute("DELETE FROM providers WHERE id = ?", (provider_id,))
        conn.commit()
    return result.rowcount > 0


def list_accounts() -> list[AccountRead]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT accounts.*, providers.friendly_name AS provider_name, providers.provider_type AS provider_type
            FROM accounts
            LEFT JOIN providers ON providers.id = accounts.provider_id
            ORDER BY providers.friendly_name, accounts.priority_group, accounts.weight DESC, accounts.friendly_name
            """
        ).fetchall()
    return [_account_from_row(row) for row in rows]


def get_account(account_id: str) -> AccountRead | None:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT accounts.*, providers.friendly_name AS provider_name, providers.provider_type AS provider_type
            FROM accounts
            LEFT JOIN providers ON providers.id = accounts.provider_id
            WHERE accounts.id = ?
            """,
            (account_id,),
        ).fetchone()
    return _account_from_row(row) if row else None


def create_account(payload: AccountCreate) -> AccountRead | None:
    if get_provider(payload.provider_id) is None:
        return None
    now = _now()
    account_id = uuid4().hex
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO accounts (
                id, provider_id, friendly_name, username, password_secret, base_url, playlist_url,
                max_simultaneous_streams, priority_group, weight, enabled, health_status,
                notes, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                account_id,
                payload.provider_id,
                payload.friendly_name,
                payload.username,
                payload.password,
                payload.base_url,
                "",
                payload.max_simultaneous_streams,
                payload.priority_group,
                payload.weight,
                int(payload.enabled),
                payload.health_status,
                payload.notes,
                now,
                now,
            ),
        )
        conn.commit()
    add_log("info", "accounts", f"Created account {payload.friendly_name}")
    return get_account(account_id)


def update_account(account_id: str, payload: AccountUpdate) -> AccountRead | None:
    existing = get_account(account_id)
    if existing is None:
        return None
    data = payload.model_dump(exclude_unset=True)
    if "provider_id" in data and get_provider(data["provider_id"]) is None:
        return None
    if "password" in data:
        if data["password"]:
            data["password_secret"] = data.pop("password")
        else:
            data.pop("password")
    if "enabled" in data:
        data["enabled"] = int(data["enabled"])
        if not data["enabled"]:
            data["health_status"] = "Disabled"
    if not data:
        return existing
    data["updated_at"] = _now()
    assignments = ", ".join(f"{key} = :{key}" for key in data)
    data["id"] = account_id
    with _connect() as conn:
        conn.execute(f"UPDATE accounts SET {assignments} WHERE id = :id", data)
        conn.commit()
    return get_account(account_id)


def delete_account(account_id: str) -> bool:
    with _connect() as conn:
        result = conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
        conn.commit()
    return result.rowcount > 0


def test_account(account_id: str) -> ConnectionTestResult | None:
    account = get_account(account_id)
    if account is None:
        return None
    now = _now()
    if not account.enabled:
        status = "Disabled"
        message = "Account is disabled."
    else:
        targets = [value for value in [account.base_url] if value]
        status = "Healthy"
        message = "No URL configured; marked healthy for manual/offline provider setup."
        for target in targets:
            ok, status, message = _check_target(target)
            if not ok:
                break
    with _connect() as conn:
        conn.execute(
            """
            UPDATE accounts
            SET health_status = ?, last_success = ?, last_failure = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, now if status == "Healthy" else account.last_success.isoformat() if account.last_success else None, now if status != "Healthy" else account.last_failure.isoformat() if account.last_failure else None, now, account_id),
        )
        conn.commit()
    add_log("info", "accounts", f"Connection test for {account.friendly_name}: {status}")
    return ConnectionTestResult(account_id=account_id, health_status=status, message=message)


def _check_target(target: str) -> tuple[bool, str, str]:
    if target.startswith(("http://", "https://")):
        try:
            request = Request(target, method="HEAD")
            with urlopen(request, timeout=3) as response:
                status_code = getattr(response, "status", 200)
        except Exception as exc:
            if isinstance(exc, URLError):
                return False, "Offline", "URL was not reachable."
            return False, "Playlist Failed", "URL check failed."
        if 200 <= status_code < 400:
            return True, "Healthy", "URL is reachable."
        if status_code in {401, 403}:
            return False, "Authentication Failed", "URL rejected the configured credentials."
        return False, "Playlist Failed", f"URL returned HTTP {status_code}."
    path = Path(target)
    if path.exists():
        return True, "Healthy", "Local path exists."
    return False, "Offline", "Local path does not exist."


def provider_account_summary() -> dict[str, float | int]:
    with _connect() as conn:
        providers = conn.execute("SELECT COUNT(*) AS count FROM providers").fetchone()["count"]
        accounts = conn.execute("SELECT COUNT(*) AS count FROM accounts").fetchone()["count"]
        healthy = conn.execute("SELECT COUNT(*) AS count FROM accounts WHERE health_status = 'Healthy' AND enabled = 1").fetchone()["count"]
        disabled = conn.execute("SELECT COUNT(*) AS count FROM accounts WHERE enabled = 0 OR health_status = 'Disabled'").fetchone()["count"]
        problem = conn.execute(
            "SELECT COUNT(*) AS count FROM accounts WHERE enabled = 1 AND health_status IN ('Degraded', 'Authentication Failed', 'Playlist Failed', 'Offline')"
        ).fetchone()["count"]
    return {"providers": providers, "accounts": accounts, "healthy_accounts": healthy, "disabled_accounts": disabled, "problem_accounts": problem}
