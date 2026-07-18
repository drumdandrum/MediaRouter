from __future__ import annotations

from datetime import datetime, timedelta
from contextlib import closing
import hashlib
import os
from pathlib import Path
import re
import sqlite3
from uuid import uuid4

from app.core.config import get_settings
from app.schemas.broker import (
    BrokerAccountUsage,
    BrokerDecision,
    DuplicateRepairResult,
    BrokerErrorDetail,
    BrokerEvaluatedCandidate,
    BrokerReservation,
    BrokerSourceSelection,
    BrokerStatus,
)
from app.services.logs import add_log


DEFAULT_RESERVATION_TTL_SECONDS = 60
DEFAULT_REUSE_WINDOW_SECONDS = int(os.getenv("MEDIA_ROUTER_RESERVATION_REUSE_WINDOW_SECONDS", "30"))
CONSUMING_STATES = ("provisional", "active")
STARTUP_BURST_SECONDS = 3
BLOCKED_HEALTH_STATUSES = {"Authentication Failed", "Playlist Failed", "Offline", "Disabled"}
PRIORITY_ORDER = {"Preferred": 0, "Secondary": 1, "Emergency": 2}


class BrokerUnavailable(Exception):
    def __init__(self, code: str, message: str, decision_reasons: list[str], evaluated_candidates: list[BrokerEvaluatedCandidate] | None = None) -> None:
        super().__init__(message)
        self.detail = BrokerErrorDetail(
            code=code,
            message=message,
            failure_code=code,
            failure_message=message,
            decision_reasons=decision_reasons,
            evaluated_candidates=evaluated_candidates or [],
        )


def _db_path() -> Path:
    return get_settings().data_dir / "media_router.db"


def _connect() -> sqlite3.Connection:
    from app.services.catalog import ensure_schema

    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    ensure_schema(conn)
    ensure_broker_schema(conn)
    return conn


def ensure_broker_schema(conn: sqlite3.Connection | None = None) -> None:
    owns_conn = conn is None
    if conn is None:
        path = _db_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS broker_reservations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reservation_id TEXT NOT NULL UNIQUE,
            catalog_item_id TEXT NOT NULL,
            source_availability_id INTEGER NOT NULL,
            provider_id TEXT NOT NULL,
            account_id TEXT NOT NULL,
            media_type TEXT NOT NULL,
            location_ref TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            released_at TEXT,
            client_label TEXT,
            client_session TEXT,
            client_fingerprint TEXT,
            reused_from_reservation_id TEXT,
            last_reused_at TEXT,
            reuse_count INTEGER NOT NULL DEFAULT 0,
            identity_type TEXT,
            last_seen_at TEXT,
            last_action TEXT NOT NULL DEFAULT 'reservation_created',
            playback_identity_key TEXT,
            stable_client_id TEXT,
            origin_identity_hash TEXT,
            request_profile TEXT,
            coalesced_reuse_count INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(catalog_item_id) REFERENCES catalog_items(internal_id) ON DELETE CASCADE,
            FOREIGN KEY(source_availability_id) REFERENCES source_availability(id) ON DELETE CASCADE,
            FOREIGN KEY(provider_id) REFERENCES providers(id) ON DELETE CASCADE,
            FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
        );
        """
    )
    columns = {row["name"] if hasattr(row, "keys") else row[1] for row in conn.execute("PRAGMA table_info(broker_reservations)").fetchall()}
    migrations = {
        "client_session": "ALTER TABLE broker_reservations ADD COLUMN client_session TEXT",
        "client_fingerprint": "ALTER TABLE broker_reservations ADD COLUMN client_fingerprint TEXT",
        "reused_from_reservation_id": "ALTER TABLE broker_reservations ADD COLUMN reused_from_reservation_id TEXT",
        "last_reused_at": "ALTER TABLE broker_reservations ADD COLUMN last_reused_at TEXT",
        "reuse_count": "ALTER TABLE broker_reservations ADD COLUMN reuse_count INTEGER NOT NULL DEFAULT 0",
        "identity_type": "ALTER TABLE broker_reservations ADD COLUMN identity_type TEXT",
        "last_seen_at": "ALTER TABLE broker_reservations ADD COLUMN last_seen_at TEXT",
        "last_action": "ALTER TABLE broker_reservations ADD COLUMN last_action TEXT NOT NULL DEFAULT 'reservation_created'",
        "playback_identity_key": "ALTER TABLE broker_reservations ADD COLUMN playback_identity_key TEXT",
        "stable_client_id": "ALTER TABLE broker_reservations ADD COLUMN stable_client_id TEXT",
        "origin_identity_hash": "ALTER TABLE broker_reservations ADD COLUMN origin_identity_hash TEXT",
        "request_profile": "ALTER TABLE broker_reservations ADD COLUMN request_profile TEXT",
        "coalesced_reuse_count": "ALTER TABLE broker_reservations ADD COLUMN coalesced_reuse_count INTEGER NOT NULL DEFAULT 0",
        "lifecycle_state": "ALTER TABLE broker_reservations ADD COLUMN lifecycle_state TEXT",
        "provisional_expires_at": "ALTER TABLE broker_reservations ADD COLUMN provisional_expires_at TEXT",
        "active_expires_at": "ALTER TABLE broker_reservations ADD COLUMN active_expires_at TEXT",
        "promoted_at": "ALTER TABLE broker_reservations ADD COLUMN promoted_at TEXT",
        "superseded_at": "ALTER TABLE broker_reservations ADD COLUMN superseded_at TEXT",
        "superseded_by_reservation_id": "ALTER TABLE broker_reservations ADD COLUMN superseded_by_reservation_id TEXT",
        "first_seen_at": "ALTER TABLE broker_reservations ADD COLUMN first_seen_at TEXT",
        "request_count": "ALTER TABLE broker_reservations ADD COLUMN request_count INTEGER NOT NULL DEFAULT 1",
        "distinct_activity_count": "ALTER TABLE broker_reservations ADD COLUMN distinct_activity_count INTEGER NOT NULL DEFAULT 0",
        "promotion_reason": "ALTER TABLE broker_reservations ADD COLUMN promotion_reason TEXT",
        "release_reason": "ALTER TABLE broker_reservations ADD COLUMN release_reason TEXT",
        "active_ttl_seconds": "ALTER TABLE broker_reservations ADD COLUMN active_ttl_seconds INTEGER",
        "provisional_hard_expires_at": "ALTER TABLE broker_reservations ADD COLUMN provisional_hard_expires_at TEXT",
    }
    applied: list[str] = []
    for column, statement in migrations.items():
        if column not in columns:
            conn.execute(statement)
            applied.append(column)
    conn.execute("""UPDATE broker_reservations SET
        identity_type=CASE WHEN client_session IS NOT NULL THEN 'explicit_session'
                           WHEN client_fingerprint IS NOT NULL THEN 'derived_fingerprint' END,
        last_seen_at=COALESCE(last_seen_at, last_reused_at, created_at),
        last_action=CASE WHEN COALESCE(reuse_count, 0) > 0 THEN 'reservation_reused' ELSE 'reservation_created' END
        WHERE identity_type IS NULL OR last_seen_at IS NULL""")
    conn.execute("""UPDATE broker_reservations SET
        lifecycle_state=COALESCE(lifecycle_state, CASE status
            WHEN 'active' THEN 'active' WHEN 'released' THEN 'released'
            WHEN 'expired' THEN 'expired' WHEN 'failed' THEN 'failed' ELSE status END),
        active_expires_at=CASE WHEN status='active' THEN COALESCE(active_expires_at, expires_at) ELSE active_expires_at END,
        first_seen_at=COALESCE(first_seen_at, created_at),
        request_count=COALESCE(request_count, 1), distinct_activity_count=COALESCE(distinct_activity_count, 0)
        WHERE lifecycle_state IS NULL OR first_seen_at IS NULL OR (status='active' AND active_expires_at IS NULL)""")
    for row in conn.execute("SELECT reservation_id, client_session, client_fingerprint FROM broker_reservations WHERE client_session IS NOT NULL OR client_fingerprint IS NOT NULL").fetchall():
        session = row["client_session"] if hasattr(row, "keys") else row[1]
        fingerprint = row["client_fingerprint"] if hasattr(row, "keys") else row[2]
        hashed_session = session if _is_hashed_identity(session) else _identity_hash(session, "explicit_session")
        hashed_fingerprint = fingerprint if _is_hashed_identity(fingerprint) else _identity_hash(fingerprint, "derived_fingerprint")
        if hashed_session != session or hashed_fingerprint != fingerprint:
            reservation_id = row["reservation_id"] if hasattr(row, "keys") else row[0]
            conn.execute("UPDATE broker_reservations SET client_session=?, client_fingerprint=? WHERE reservation_id=?", (hashed_session, hashed_fingerprint, reservation_id))
    identity_rows = conn.execute("""SELECT reservation_id, catalog_item_id, media_type, client_session,
        client_fingerprint, status, COALESCE(last_seen_at, last_reused_at, created_at) AS identity_seen
        FROM broker_reservations WHERE playback_identity_key IS NULL
        AND (client_session IS NOT NULL OR client_fingerprint IS NOT NULL)
        ORDER BY identity_seen DESC, id DESC""").fetchall()
    active_keys: set[str] = set()
    migration_now = _now().isoformat()
    for row in identity_rows:
        identity_type = "explicit_session" if row["client_session"] else "derived_fingerprint"
        identity = row["client_session"] or row["client_fingerprint"]
        key = _playback_identity_key(row["catalog_item_id"], row["media_type"], identity_type, identity)
        existing_key_owner = conn.execute("""SELECT reservation_id FROM broker_reservations
            WHERE lifecycle_state IN ('provisional','active') AND playback_identity_key=? AND reservation_id != ? LIMIT 1""",
            (key, row["reservation_id"])).fetchone() if row["status"] == "active" else None
        if row["status"] == "active" and (key in active_keys or existing_key_owner is not None):
            conn.execute("""UPDATE broker_reservations SET status='released', released_at=?,
                last_action='duplicate_released' WHERE reservation_id=?""", (migration_now, row["reservation_id"]))
        else:
            conn.execute("UPDATE broker_reservations SET playback_identity_key=? WHERE reservation_id=?",
                         (key, row["reservation_id"]))
            if row["status"] == "active":
                active_keys.add(key)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS broker_reservation_identity_aliases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alias_id TEXT NOT NULL UNIQUE,
            reservation_id TEXT NOT NULL,
            catalog_item_id TEXT NOT NULL,
            media_type TEXT NOT NULL,
            identity_type TEXT NOT NULL,
            identity_hash TEXT NOT NULL,
            origin_identity_hash TEXT,
            request_profile TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            FOREIGN KEY(reservation_id) REFERENCES broker_reservations(reservation_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_broker_reservations_status ON broker_reservations(status);
        CREATE INDEX IF NOT EXISTS idx_broker_reservations_account ON broker_reservations(account_id, status);
        CREATE INDEX IF NOT EXISTS idx_broker_reservations_expires ON broker_reservations(expires_at);
        CREATE INDEX IF NOT EXISTS idx_broker_reservations_reuse_session ON broker_reservations(catalog_item_id, media_type, status, client_session, created_at);
        CREATE INDEX IF NOT EXISTS idx_broker_reservations_reuse_fingerprint ON broker_reservations(catalog_item_id, media_type, status, client_fingerprint, created_at);
        DROP INDEX IF EXISTS uq_broker_active_playback_identity;
        CREATE UNIQUE INDEX IF NOT EXISTS uq_broker_consuming_playback_identity
            ON broker_reservations(playback_identity_key)
            WHERE lifecycle_state IN ('provisional','active') AND playback_identity_key IS NOT NULL;
        CREATE INDEX IF NOT EXISTS idx_broker_alias_reservation
            ON broker_reservation_identity_aliases(reservation_id, active);
        """
    )
    alias_columns = {row[1] for row in conn.execute("PRAGMA table_info(broker_reservation_identity_aliases)").fetchall()}
    if "catalog_item_id" not in alias_columns:
        conn.execute("ALTER TABLE broker_reservation_identity_aliases ADD COLUMN catalog_item_id TEXT")
    if "media_type" not in alias_columns:
        conn.execute("ALTER TABLE broker_reservation_identity_aliases ADD COLUMN media_type TEXT")
    conn.execute("""UPDATE broker_reservation_identity_aliases SET
        catalog_item_id=(SELECT catalog_item_id FROM broker_reservations r WHERE r.reservation_id=broker_reservation_identity_aliases.reservation_id),
        media_type=(SELECT media_type FROM broker_reservations r WHERE r.reservation_id=broker_reservation_identity_aliases.reservation_id)
        WHERE catalog_item_id IS NULL OR media_type IS NULL""")
    conn.execute("DROP INDEX IF EXISTS uq_broker_active_identity_alias")
    conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS uq_broker_active_identity_alias_scoped
        ON broker_reservation_identity_aliases(catalog_item_id, media_type, identity_type, identity_hash)
        WHERE active=1""")
    alias_now = _now().isoformat()
    for row in conn.execute("""SELECT reservation_id, identity_type, client_session, client_fingerprint,
        stable_client_id, origin_identity_hash, request_profile, created_at, COALESCE(last_seen_at, created_at) AS seen
        FROM broker_reservations WHERE lifecycle_state IN ('provisional','active') AND NOT EXISTS (
            SELECT 1 FROM broker_reservation_identity_aliases aliases
            WHERE aliases.reservation_id=broker_reservations.reservation_id)""").fetchall():
        identity = row["stable_client_id"] or row["client_session"] or row["client_fingerprint"]
        identity_type = "stable_client_id" if row["stable_client_id"] else row["identity_type"]
        if identity and identity_type:
            conn.execute("""INSERT OR IGNORE INTO broker_reservation_identity_aliases
                (alias_id,reservation_id,catalog_item_id,media_type,identity_type,identity_hash,origin_identity_hash,request_profile,active,first_seen_at,last_seen_at)
                VALUES (?, ?, (SELECT catalog_item_id FROM broker_reservations WHERE reservation_id=?),
                    (SELECT media_type FROM broker_reservations WHERE reservation_id=?), ?,?,?,?,1,?,?)""",
                (uuid4().hex, row["reservation_id"], row["reservation_id"], row["reservation_id"], identity_type, identity,
                row["origin_identity_hash"], row["request_profile"], row["created_at"] or alias_now, row["seen"] or alias_now))
    conn.commit()
    if applied:
        add_log("info", "broker", f"Applied broker reservation schema migration: {', '.join(applied)}")
    if owns_conn:
        conn.close()


def _now() -> datetime:
    return datetime.utcnow()


def _normalize_media_type(media_type: str | None) -> str | None:
    if media_type in {None, ""}:
        return None
    aliases = {"live": "channel", "series": "episode"}
    return aliases.get(media_type, media_type)


def _identity_hash(value: str | None, identity_type: str) -> str | None:
    if not value:
        return None
    return hashlib.sha256(f"{identity_type}:{value}".encode("utf-8")).hexdigest()[:32]


def _playback_identity_key(catalog_item_id: str, media_type: str, identity_type: str, identity: str | None) -> str | None:
    if not identity:
        return None
    normalized_media_type = _normalize_media_type(media_type) or media_type
    raw = f"{catalog_item_id}|{normalized_media_type}|{identity_type}|{identity}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]


def _is_hashed_identity(value: str | None) -> bool:
    return bool(value and re.fullmatch(r"[0-9a-f]{32}", value))


def _masked_identity(value: str | None) -> str | None:
    return f"{value[:8]}…" if value else None


def _redact_ref(value: str) -> str:
    return re.sub(r"(/(?:live|movie|series)/)[^/]+/[^/]+/", r"\1[redacted]/[redacted]/", value, flags=re.IGNORECASE)


def _lease_policy(media_type: str | None, active_ttl_override: int | None = None) -> dict[str, int | bool]:
    settings = __import__("app.services.settings", fromlist=["get_app_settings"]).get_app_settings()
    route = "live" if _normalize_media_type(media_type) == "channel" else _normalize_media_type(media_type) or "movie"
    return {
        "provisional_ttl": getattr(settings, f"{route}_provisional_ttl_seconds"),
        "promotion_age": getattr(settings, f"{route}_promotion_minimum_age_seconds"),
        "promotion_threshold": getattr(settings, f"{route}_promotion_request_threshold"),
        "active_ttl": active_ttl_override or getattr(settings, f"{route}_active_ttl_seconds"),
        "supersession": getattr(settings, "live_same_identity_supersession" if route == "live" else f"{route}_provisional_supersession"),
        "sliding": settings.active_lease_sliding_renewal,
    }


def _sync_legacy_expiry(conn: sqlite3.Connection, reservation_id: str) -> None:
    conn.execute("""UPDATE broker_reservations SET expires_at=CASE
        WHEN lifecycle_state='provisional' THEN provisional_expires_at
        WHEN lifecycle_state='active' THEN active_expires_at ELSE expires_at END
        WHERE reservation_id=?""", (reservation_id,))


def expire_reservations(conn: sqlite3.Connection | None = None, *, commit: bool = True) -> int:
    owns_conn = conn is None
    if conn is None:
        conn = _connect()
    now = _now().isoformat()
    due = conn.execute("""SELECT reservation_id,catalog_item_id,media_type,account_id,lifecycle_state
        FROM broker_reservations WHERE lifecycle_state IN ('provisional','active')
        AND CASE WHEN lifecycle_state='provisional' THEN COALESCE(provisional_expires_at,expires_at)
                 ELSE COALESCE(active_expires_at,expires_at) END <= ?""", (now,)).fetchall()
    result = conn.execute(
        """
        UPDATE broker_reservations
        SET status = 'expired', lifecycle_state='expired', release_reason=CASE
            WHEN lifecycle_state='provisional' THEN 'provisional_ttl_elapsed'
            ELSE 'active_ttl_elapsed' END, last_action='reservation_expired'
        WHERE lifecycle_state IN ('provisional','active')
          AND CASE WHEN lifecycle_state='provisional'
                   THEN COALESCE(provisional_expires_at, expires_at)
                   ELSE COALESCE(active_expires_at, expires_at) END <= ?
        """,
        (now,),
    )
    conn.execute("""UPDATE broker_reservation_identity_aliases SET active=0
        WHERE active=1 AND reservation_id IN (
            SELECT reservation_id FROM broker_reservations
            WHERE lifecycle_state NOT IN ('provisional','active'))""")
    if commit:
        conn.commit()
    expired = result.rowcount
    for row in due:
        add_log("info", "broker", f"reservation_expired reservation={row['reservation_id']} catalog_item={row['catalog_item_id']} media_type={row['media_type']} lifecycle_state={row['lifecycle_state']} reason={'provisional_ttl_elapsed' if row['lifecycle_state']=='provisional' else 'active_ttl_elapsed'}")
    if owns_conn:
        conn.close()
    return expired


def _active_counts(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        """
        SELECT account_id, COUNT(*) AS active_count
        FROM broker_reservations
        WHERE lifecycle_state IN ('provisional','active')
        GROUP BY account_id
        """
    ).fetchall()
    return {row["account_id"]: row["active_count"] for row in rows}


def _reservation_from_row(row: sqlite3.Row) -> BrokerReservation:
    client_session = row["client_session"] if "client_session" in row.keys() else None
    client_fingerprint = row["client_fingerprint"] if "client_fingerprint" in row.keys() else None
    stable_client_id = row["stable_client_id"] if "stable_client_id" in row.keys() else None
    identity_type = row["identity_type"] if "identity_type" in row.keys() and row["identity_type"] else ("explicit_session" if client_session else "derived_fingerprint" if client_fingerprint else None)
    identity_value = (client_session if identity_type == "explicit_session" else stable_client_id
                      if identity_type == "stable_client_id" else client_fingerprint
                      if identity_type == "derived_fingerprint" else None)
    lifecycle = row["lifecycle_state"] if "lifecycle_state" in row.keys() and row["lifecycle_state"] else row["status"]
    def parsed(name: str) -> datetime | None:
        return datetime.fromisoformat(row[name]) if name in row.keys() and row[name] else None
    effective_expiry = (row["provisional_expires_at"] if lifecycle == "provisional" and "provisional_expires_at" in row.keys()
                        else row["active_expires_at"] if lifecycle == "active" and "active_expires_at" in row.keys() else None)
    effective_expiry = effective_expiry if effective_expiry else row["expires_at"]
    return BrokerReservation(
        reservation_id=row["reservation_id"],
        catalog_item_id=row["catalog_item_id"],
        catalog_title=row["catalog_title"] if "catalog_title" in row.keys() else None,
        source_availability_id=row["source_availability_id"],
        provider_id=row["provider_id"],
        provider_name=row["provider_name"] if "provider_name" in row.keys() else None,
        account_id=row["account_id"],
        account_name=row["account_name"] if "account_name" in row.keys() else None,
        media_type=row["media_type"],
        location_ref=_redact_ref(row["location_ref"]),
        status=lifecycle,
        created_at=datetime.fromisoformat(row["created_at"]),
        expires_at=datetime.fromisoformat(effective_expiry),
        released_at=datetime.fromisoformat(row["released_at"]) if row["released_at"] else None,
        client_label=row["client_label"],
        client_session=client_session,
        last_reused_at=datetime.fromisoformat(row["last_reused_at"]) if "last_reused_at" in row.keys() and row["last_reused_at"] else None,
        reuse_count=int(row["reuse_count"] or 0) if "reuse_count" in row.keys() else 0,
        identity_type=identity_type,
        masked_client_identity=_masked_identity(identity_value),
        last_seen_at=datetime.fromisoformat(row["last_seen_at"]) if "last_seen_at" in row.keys() and row["last_seen_at"] else (datetime.fromisoformat(row["last_reused_at"]) if "last_reused_at" in row.keys() and row["last_reused_at"] else datetime.fromisoformat(row["created_at"])),
        last_action=row["last_action"] if "last_action" in row.keys() and row["last_action"] else "reservation_created",
        duplicate_warning=bool(row["duplicate_warning"]) if "duplicate_warning" in row.keys() else False,
        alias_count=int(row["alias_count"] or 0) if "alias_count" in row.keys() else 0,
        coalesced_reuse_count=int(row["coalesced_reuse_count"] or 0) if "coalesced_reuse_count" in row.keys() else 0,
        startup_coalesced=bool(row["coalesced_reuse_count"]) if "coalesced_reuse_count" in row.keys() else False,
        lifecycle_state=lifecycle,
        provisional_expires_at=parsed("provisional_expires_at"), active_expires_at=parsed("active_expires_at"),
        promoted_at=parsed("promoted_at"), superseded_at=parsed("superseded_at"),
        superseded_by_reservation_id=row["superseded_by_reservation_id"] if "superseded_by_reservation_id" in row.keys() else None,
        first_seen_at=parsed("first_seen_at") or datetime.fromisoformat(row["created_at"]),
        request_count=int(row["request_count"] or 1) if "request_count" in row.keys() else 1,
        distinct_activity_count=int(row["distinct_activity_count"] or 0) if "distinct_activity_count" in row.keys() else 0,
        promotion_reason=row["promotion_reason"] if "promotion_reason" in row.keys() else None,
        release_reason=row["release_reason"] if "release_reason" in row.keys() else None,
    )


def _reservation_query(where: str = "", params: tuple = ()) -> list[BrokerReservation]:
    with closing(_connect()) as conn:
        expire_reservations(conn)
        rows = conn.execute(
            f"""
            SELECT
                broker_reservations.*,
                catalog_items.title AS catalog_title,
                providers.friendly_name AS provider_name,
                accounts.friendly_name AS account_name
                , EXISTS (
                    SELECT 1 FROM broker_reservations duplicate
                    WHERE duplicate.lifecycle_state IN ('provisional','active') AND duplicate.reservation_id != broker_reservations.reservation_id
                    AND duplicate.catalog_item_id=broker_reservations.catalog_item_id
                    AND duplicate.media_type=broker_reservations.media_type
                    AND ((broker_reservations.client_session IS NOT NULL AND duplicate.client_session=broker_reservations.client_session)
                      OR (broker_reservations.client_fingerprint IS NOT NULL AND duplicate.client_fingerprint=broker_reservations.client_fingerprint))
                ) AS duplicate_warning
                , (SELECT COUNT(*) FROM broker_reservation_identity_aliases aliases
                   WHERE aliases.reservation_id=broker_reservations.reservation_id AND aliases.active=1) AS alias_count
            FROM broker_reservations
            LEFT JOIN catalog_items ON catalog_items.internal_id = broker_reservations.catalog_item_id
            LEFT JOIN providers ON providers.id = broker_reservations.provider_id
            LEFT JOIN accounts ON accounts.id = broker_reservations.account_id
            {where}
            ORDER BY
                CASE broker_reservations.status
                    WHEN 'provisional' THEN 0
                    WHEN 'active' THEN 1
                    WHEN 'expired' THEN 2
                    WHEN 'released' THEN 3
                    WHEN 'superseded' THEN 4
                    ELSE 5
                END,
                broker_reservations.created_at DESC
            LIMIT 500
            """,
            params,
        ).fetchall()
    return [_reservation_from_row(row) for row in rows]


def list_reservations() -> list[BrokerReservation]:
    return _reservation_query()


def get_raw_reservation_location_ref(reservation_id: str) -> str | None:
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT location_ref FROM broker_reservations WHERE reservation_id = ?",
            (reservation_id,),
        ).fetchone()
    return row["location_ref"] if row else None


def _reservation_detail_row(conn: sqlite3.Connection, reservation_id: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT
            broker_reservations.*,
            catalog_items.title AS catalog_title,
            providers.friendly_name AS provider_name,
            accounts.friendly_name AS account_name
            , EXISTS (
                SELECT 1 FROM broker_reservations duplicate
                WHERE duplicate.lifecycle_state IN ('provisional','active') AND duplicate.reservation_id != broker_reservations.reservation_id
                AND duplicate.catalog_item_id=broker_reservations.catalog_item_id
                AND duplicate.media_type=broker_reservations.media_type
                AND ((broker_reservations.client_session IS NOT NULL AND duplicate.client_session=broker_reservations.client_session)
                  OR (broker_reservations.client_fingerprint IS NOT NULL AND duplicate.client_fingerprint=broker_reservations.client_fingerprint))
            ) AS duplicate_warning
            , (SELECT COUNT(*) FROM broker_reservation_identity_aliases aliases
               WHERE aliases.reservation_id=broker_reservations.reservation_id AND aliases.active=1) AS alias_count
        FROM broker_reservations
        LEFT JOIN catalog_items ON catalog_items.internal_id = broker_reservations.catalog_item_id
        LEFT JOIN providers ON providers.id = broker_reservations.provider_id
        LEFT JOIN accounts ON accounts.id = broker_reservations.account_id
        WHERE broker_reservations.reservation_id = ?
        """,
        (reservation_id,),
    ).fetchone()


def _register_identity_alias(
    conn: sqlite3.Connection, *, reservation_id: str, identity_type: str, identity_hash: str,
    origin_identity_hash: str | None, request_profile: str | None, seen_at: str,
) -> None:
    reservation = conn.execute("SELECT catalog_item_id, media_type FROM broker_reservations WHERE reservation_id=?",
                               (reservation_id,)).fetchone()
    conn.execute("""INSERT INTO broker_reservation_identity_aliases
        (alias_id,reservation_id,catalog_item_id,media_type,identity_type,identity_hash,origin_identity_hash,request_profile,active,first_seen_at,last_seen_at)
        VALUES (?,?,?,?,?,?,?,?,1,?,?)
        ON CONFLICT(catalog_item_id, media_type, identity_type, identity_hash) WHERE active=1 DO UPDATE SET
            last_seen_at=excluded.last_seen_at""",
        (uuid4().hex, reservation_id, reservation["catalog_item_id"], reservation["media_type"],
         identity_type, identity_hash, origin_identity_hash,
         request_profile, seen_at, seen_at))


def _find_alias_reservation(conn: sqlite3.Connection, *, catalog_item_id: str, media_type: str | None,
                            identity_type: str | None, identity_hash: str | None) -> sqlite3.Row | None:
    if not identity_type or not identity_hash:
        return None
    return conn.execute("""SELECT reservations.* FROM broker_reservation_identity_aliases aliases
        JOIN broker_reservations reservations ON reservations.reservation_id=aliases.reservation_id
        WHERE aliases.active=1 AND reservations.lifecycle_state IN ('provisional','active')
          AND aliases.catalog_item_id=? AND aliases.media_type=?
          AND aliases.identity_type=? AND aliases.identity_hash=? LIMIT 1""",
        (catalog_item_id, _normalize_media_type(media_type), identity_type, identity_hash)).fetchone()


def _find_startup_coalescing_candidates(
    conn: sqlite3.Connection, *, catalog_item_id: str, media_type: str | None,
    origin_identity_hash: str | None, request_profile: str | None, window_seconds: int,
) -> tuple[list[sqlite3.Row], str]:
    if not origin_identity_hash:
        return [], "no_origin"
    if window_seconds <= 0:
        return [], "window_disabled"
    cutoff = (_now() - timedelta(seconds=window_seconds)).isoformat()
    active = conn.execute("""SELECT * FROM broker_reservations
        WHERE lifecycle_state IN ('provisional','active') AND catalog_item_id=? AND media_type=? AND origin_identity_hash=?
        ORDER BY COALESCE(last_seen_at, created_at) DESC""",
        (catalog_item_id, _normalize_media_type(media_type), origin_identity_hash)).fetchall()
    recent = [row for row in active if (row["last_seen_at"] or row["created_at"]) >= cutoff]
    candidates = [row for row in recent if row["client_session"] is None]
    if candidates:
        return candidates, "candidate"
    if recent:
        return [], "explicit_session_conflict"
    if active:
        return [], "outside_window"
    return [], "no_match"


def _find_reusable_reservation(
    conn: sqlite3.Connection,
    *,
    catalog_item_id: str,
    media_type: str | None,
    client_session: str | None,
    client_fingerprint: str | None,
    reuse_window_seconds: int,
) -> sqlite3.Row | None:
    normalized_media_type = _normalize_media_type(media_type)
    if client_session:
        return conn.execute(
            """
            SELECT *
            FROM broker_reservations
            WHERE lifecycle_state IN ('provisional','active')
              AND catalog_item_id = ?
              AND (? IS NULL OR media_type = ?)
              AND client_session = ?
            ORDER BY COALESCE(last_seen_at, last_reused_at, created_at) DESC
            LIMIT 1
            """,
            (catalog_item_id, normalized_media_type, normalized_media_type, client_session),
        ).fetchone()
    if client_fingerprint:
        return conn.execute(
            """
            SELECT *
            FROM broker_reservations
            WHERE lifecycle_state IN ('provisional','active')
              AND catalog_item_id = ?
              AND (? IS NULL OR media_type = ?)
              AND client_fingerprint = ?
            ORDER BY COALESCE(last_seen_at, last_reused_at, created_at) DESC
            LIMIT 1
            """,
            (catalog_item_id, normalized_media_type, normalized_media_type, client_fingerprint),
        ).fetchone()
    return None


def _source_rows(conn: sqlite3.Connection, catalog_item_id: str, media_type: str | None) -> list[sqlite3.Row]:
    media_type = _normalize_media_type(media_type)
    where_media = "AND source_availability.media_type = ?" if media_type else ""
    params: tuple = (catalog_item_id, media_type) if media_type else (catalog_item_id,)
    return conn.execute(
        f"""
        SELECT
            source_availability.*,
            catalog_items.title AS catalog_title,
            providers.friendly_name AS provider_name,
            providers.provider_type AS provider_type,
            providers.enabled AS provider_enabled,
            providers.health_status AS provider_health_status,
            accounts.friendly_name AS account_name,
            accounts.enabled AS account_enabled,
            accounts.health_status AS account_health_status,
            accounts.priority_group AS priority_group,
            accounts.weight AS weight,
            accounts.max_simultaneous_streams AS max_simultaneous_streams
        FROM source_availability
        LEFT JOIN catalog_items ON catalog_items.internal_id = source_availability.catalog_internal_id
        LEFT JOIN providers ON providers.id = source_availability.provider_id
        LEFT JOIN accounts ON accounts.id = source_availability.account_id
        WHERE source_availability.catalog_internal_id = ?
        {where_media}
        ORDER BY source_availability.id
        """,
        params,
    ).fetchall()


def _source_selection_from_row(row: sqlite3.Row, active_count: int) -> BrokerSourceSelection:
    return BrokerSourceSelection(
        source_availability_id=row["id"],
        catalog_item_id=row["catalog_internal_id"],
        catalog_title=row["catalog_title"],
        media_type=row["media_type"],
        location_ref=_redact_ref(row["location_ref"]),
        provider_id=row["provider_id"],
        provider_name=row["provider_name"],
        provider_type=row["provider_type"],
        account_id=row["account_id"],
        account_name=row["account_name"],
        priority_group=row["priority_group"],
        weight=row["weight"],
        active_reservations=active_count,
        max_simultaneous_streams=row["max_simultaneous_streams"],
    )


def _candidate_from_row(row: sqlite3.Row, active_count: int, reason: str, reason_detail: str, selected: bool = False) -> BrokerEvaluatedCandidate:
    return BrokerEvaluatedCandidate(
        source_availability_id=row["id"],
        catalog_item_id=row["catalog_internal_id"],
        media_type=row["media_type"],
        provider_id=row["provider_id"],
        provider_name=row["provider_name"],
        account_id=row["account_id"],
        account_name=row["account_name"],
        priority_group=row["priority_group"],
        weight=row["weight"],
        active_reservations=active_count,
        max_simultaneous_streams=row["max_simultaneous_streams"],
        source_enabled=bool(row["enabled"]),
        provider_enabled=bool(row["provider_enabled"]) if row["provider_enabled"] is not None else False,
        account_enabled=bool(row["account_enabled"]) if row["account_enabled"] is not None else False,
        provider_health_status=row["provider_health_status"],
        account_health_status=row["account_health_status"],
        selected=selected,
        reason=reason,
        reason_detail=reason_detail,
    )


def _classify_no_source(rows: list[sqlite3.Row], decision_reasons: list[str], evaluated_candidates: list[BrokerEvaluatedCandidate]) -> BrokerUnavailable:
    if not rows:
        return BrokerUnavailable("no_sources", "No catalog sources available.", decision_reasons, evaluated_candidates)
    if all(not bool(row["enabled"]) for row in rows):
        return BrokerUnavailable("all_disabled", "All catalog sources are disabled.", decision_reasons, evaluated_candidates)
    if all(
        (not row["provider_id"])
        or (not row["account_id"])
        or (not bool(row["provider_enabled"]))
        or (not bool(row["account_enabled"]))
        or row["provider_health_status"] in BLOCKED_HEALTH_STATUSES
        or row["account_health_status"] in BLOCKED_HEALTH_STATUSES
        for row in rows
        if bool(row["enabled"])
    ):
        return BrokerUnavailable("all_unhealthy", "No healthy accounts found.", decision_reasons, evaluated_candidates)
    return BrokerUnavailable("all_at_capacity", "All matching accounts are at capacity.", decision_reasons, evaluated_candidates)


def _reused_decision(
    conn: sqlite3.Connection,
    *,
    reusable: sqlite3.Row,
    ttl: int,
    decision_reasons: list[str],
    evaluated_candidates: list[BrokerEvaluatedCandidate],
    reuse_reason: str,
    reuse_action: str = "reservation_reused",
    alias_identity_type: str | None = None,
    alias_identity_hash: str | None = None,
    alias_origin_identity_hash: str | None = None,
    alias_request_profile: str | None = None,
    meaningful_activity: bool = True,
) -> BrokerDecision:
    rows = _source_rows(conn, reusable["catalog_item_id"], reusable["media_type"])
    selected_row = next((row for row in rows if int(row["id"]) == int(reusable["source_availability_id"])), None)
    if selected_row is None:
        raise BrokerUnavailable("no_sources", "Reusable reservation source is no longer available.", decision_reasons, evaluated_candidates)
    active_counts = _active_counts(conn)
    active_count = active_counts.get(reusable["account_id"], 0)
    selected_candidate = _candidate_from_row(
        selected_row,
        active_count,
        "selected",
        reuse_reason,
        selected=True,
    )
    evaluated_candidates.append(selected_candidate)
    now = _now()
    seen_at = now.isoformat()
    created = datetime.fromisoformat(reusable["created_at"])
    age_seconds = max((now - created).total_seconds(), 0)
    policy = _lease_policy(reusable["media_type"], ttl)
    meaningful = meaningful_activity and age_seconds >= STARTUP_BURST_SECONDS
    new_distinct = int(reusable["distinct_activity_count"] or 0) + (1 if meaningful else 0)
    lifecycle = reusable["lifecycle_state"]
    promote = (lifecycle == "provisional" and age_seconds >= int(policy["promotion_age"])
               and new_distinct >= int(policy["promotion_threshold"]))
    active_expiry = (now + timedelta(seconds=int(policy["active_ttl"]))).isoformat()
    conn.execute(
        """
        UPDATE broker_reservations
        SET last_reused_at = ?, last_seen_at = ?,
            reuse_count = COALESCE(reuse_count, 0) + 1,
            coalesced_reuse_count = COALESCE(coalesced_reuse_count, 0) + ?,
            request_count=COALESCE(request_count,1)+1,
            distinct_activity_count=?, lifecycle_state=CASE WHEN ? THEN 'active' ELSE lifecycle_state END,
            status=CASE WHEN ? THEN 'active' ELSE status END,
            promoted_at=CASE WHEN ? THEN ? ELSE promoted_at END,
            promotion_reason=CASE WHEN ? THEN 'meaningful_activity_threshold' ELSE promotion_reason END,
            active_expires_at=CASE
                WHEN ? THEN ?
                WHEN lifecycle_state='active' AND ? AND ? THEN ?
                ELSE active_expires_at END,
            last_action=CASE WHEN ? THEN 'reservation_promoted' ELSE ? END
        WHERE reservation_id = ?
        """,
        (seen_at, seen_at, 1 if reuse_action == "reservation_coalesced" else 0,
         new_distinct, promote, promote, promote, seen_at, promote,
         promote, active_expiry, bool(policy["sliding"]), meaningful, active_expiry,
         promote, reuse_action, reusable["reservation_id"]),
    )
    _sync_legacy_expiry(conn, reusable["reservation_id"])
    if alias_identity_type and alias_identity_hash:
        _register_identity_alias(conn, reservation_id=reusable["reservation_id"],
            identity_type=alias_identity_type, identity_hash=alias_identity_hash,
            origin_identity_hash=alias_origin_identity_hash, request_profile=alias_request_profile,
            seen_at=seen_at)
    reservation_row = _reservation_detail_row(conn, reusable["reservation_id"])
    if reservation_row is None:
        raise BrokerUnavailable("no_sources", "Reusable reservation was not available.", decision_reasons, evaluated_candidates)
    conn.commit()
    selection = _source_selection_from_row(selected_row, active_count)
    reservation = _reservation_from_row(reservation_row)
    decision_reasons.append(reuse_reason)
    add_log("info", "broker", f"{reuse_action} identity_type={reservation.identity_type} identity={reservation.masked_client_identity} catalog_item={selection.catalog_item_id} account={selection.account_name}")
    if promote:
        add_log("info", "broker", f"reservation_promoted reservation={reservation.reservation_id} catalog_item={selection.catalog_item_id} account={selection.account_name} age={int(age_seconds)} request_count={reservation.request_count} reason=meaningful_activity_threshold")
    elif lifecycle == "provisional":
        add_log("info", "broker", f"reservation_provisional_reused reservation={reservation.reservation_id} catalog_item={selection.catalog_item_id} account={selection.account_name} lifecycle_state=provisional age={int(age_seconds)} request_count={reservation.request_count}")
        reason = "minimum_age" if age_seconds < int(policy["promotion_age"]) else "activity_threshold"
        add_log("info", "broker", f"promotion_not_ready reservation={reservation.reservation_id} catalog_item={selection.catalog_item_id} age={int(age_seconds)} request_count={reservation.request_count} reason={reason}")
    return BrokerDecision(
        selected_source=selection,
        reservation=reservation,
        stream_url=selection.location_ref,
        expires_at=reservation.expires_at,
        reservation_ttl_seconds=ttl,
        decision_reason=reuse_reason,
        decision_reasons=decision_reasons,
        evaluated_candidates=evaluated_candidates,
        reservation_created=False,
        reservation_reused=True,
        reuse_reason=reuse_reason,
    )


def _supersession_candidate(conn: sqlite3.Connection, *, catalog_item_id: str, media_type: str | None,
                            identity_type: str | None, identity: str | None) -> tuple[sqlite3.Row | None, str]:
    normalized = _normalize_media_type(media_type)
    if identity_type not in {"explicit_session", "stable_client_id"} or not identity:
        return None, "identity_not_high_confidence"
    column = "client_session" if identity_type == "explicit_session" else "stable_client_id"
    if normalized == "channel":
        family_sql = "media_type='channel'"
    elif normalized in {"movie", "episode"}:
        family_sql = "media_type IN ('movie','episode') AND lifecycle_state='provisional'"
    else:
        return None, "unsupported_media_family"
    candidates = conn.execute(f"""SELECT * FROM broker_reservations
        WHERE lifecycle_state IN ('provisional','active') AND catalog_item_id != ?
          AND {family_sql} AND {column}=? ORDER BY created_at DESC""", (catalog_item_id, identity)).fetchall()
    if len(candidates) != 1:
        return None, "no_match" if not candidates else "ambiguous"
    return candidates[0], "safe_unique_match"


def resolve_source(
    catalog_item_id: str,
    media_type: str | None = None,
    client_label: str | None = None,
    client_session: str | None = None,
    client_fingerprint: str | None = None,
    origin_identity: str | None = None,
    stable_client_id: str | None = None,
    request_profile: str | None = None,
    startup_coalescing_window_seconds: int = 90,
    allow_reservation_reuse: bool = False,
    reuse_window_seconds: int = DEFAULT_REUSE_WINDOW_SECONDS,
    reserve: bool = True,
    reservation_ttl_seconds: int | None = None,
    lifecycle_enabled: bool = False,
    meaningful_activity: bool = True,
) -> BrokerDecision:
    if client_session:
        client_session = _identity_hash(client_session, "explicit_session")
        client_fingerprint = None
        stable_client_id = None
    elif stable_client_id:
        stable_client_id = _identity_hash(stable_client_id, "stable_client_id")
        client_fingerprint = None
    elif client_fingerprint:
        client_fingerprint = _identity_hash(client_fingerprint, "derived_fingerprint")
    origin_identity_hash = _identity_hash(origin_identity, "origin_identity") if origin_identity else None
    identity_type = ("explicit_session" if client_session else "stable_client_id" if stable_client_id
                     else "derived_fingerprint" if client_fingerprint else None)
    identity = client_session or stable_client_id or client_fingerprint
    playback_identity_key = _playback_identity_key(
        catalog_item_id, media_type or "", identity_type, identity
    ) if identity_type else None
    policy = _lease_policy(media_type, reservation_ttl_seconds)
    ttl = int(policy["active_ttl"]) if lifecycle_enabled else (reservation_ttl_seconds or DEFAULT_RESERVATION_TTL_SECONDS)
    decision_reasons = [f"TTL set to {ttl} seconds.", "Expired reservations were ignored before selection."]
    with closing(_connect()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        expire_reservations(conn, commit=False)
        rows = _source_rows(conn, catalog_item_id, media_type)
        if media_type:
            decision_reasons.append(f"Filtered sources by media type {_normalize_media_type(media_type)}.")
        evaluated_candidates: list[BrokerEvaluatedCandidate] = []
        if not rows:
            raise _classify_no_source(rows, decision_reasons, evaluated_candidates)
        decision_reasons.append(f"Found {len(rows)} source record(s) for the catalog item.")
        if allow_reservation_reuse:
            reusable = _find_alias_reservation(conn, catalog_item_id=catalog_item_id, media_type=media_type,
                                                identity_type=identity_type, identity_hash=identity)
            if reusable is not None:
                primary_identity = (reusable["client_session"] if identity_type == "explicit_session"
                                    else reusable["stable_client_id"] if identity_type == "stable_client_id"
                                    else reusable["client_fingerprint"])
                via_alias = primary_identity != identity
                return _reused_decision(
                    conn, reusable=reusable, ttl=ttl, decision_reasons=decision_reasons,
                    evaluated_candidates=evaluated_candidates,
                    reuse_reason=(f"Reused active reservation by registered {identity_type} alias."
                                  if via_alias else f"Reused active reservation by exact {identity_type}."),
                    reuse_action="reservation_reused_via_alias" if via_alias else "reservation_reused",
                    meaningful_activity=meaningful_activity,
                )
            reusable = _find_reusable_reservation(
                conn,
                catalog_item_id=catalog_item_id,
                media_type=media_type,
                client_session=client_session,
                client_fingerprint=client_fingerprint,
                reuse_window_seconds=reuse_window_seconds,
            )
            if reusable is not None:
                identity_reason = "explicit_session" if client_session else "derived_fingerprint"
                return _reused_decision(
                    conn,
                    reusable=reusable,
                    ttl=ttl,
                    decision_reasons=decision_reasons,
                    evaluated_candidates=evaluated_candidates,
                    reuse_reason=f"Reused active reservation for its full lifetime by {identity_reason}.",
                    meaningful_activity=meaningful_activity,
                )
            if identity_type == "derived_fingerprint":
                coalescing_candidates, coalescing_reason = _find_startup_coalescing_candidates(
                    conn, catalog_item_id=catalog_item_id, media_type=media_type,
                    origin_identity_hash=origin_identity_hash, request_profile=request_profile,
                    window_seconds=startup_coalescing_window_seconds,
                )
                if len(coalescing_candidates) == 1:
                    add_log("info", "broker", (
                        f"startup_coalescing origin={_masked_identity(origin_identity_hash)} "
                        f"window_seconds={startup_coalescing_window_seconds} coalescing_candidate_count=1 "
                        f"result=reused candidate={coalescing_candidates[0]['reservation_id']}"
                    ))
                    return _reused_decision(
                        conn, reusable=coalescing_candidates[0], ttl=ttl,
                        decision_reasons=decision_reasons, evaluated_candidates=evaluated_candidates,
                        reuse_reason="Coalesced one unambiguous recent Emby startup identity.",
                        reuse_action="reservation_coalesced", alias_identity_type=identity_type,
                        alias_identity_hash=identity, alias_origin_identity_hash=origin_identity_hash,
                        alias_request_profile=request_profile,
                        meaningful_activity=meaningful_activity,
                    )
                if len(coalescing_candidates) > 1:
                    decision_reasons.append("Startup coalescing was skipped because multiple candidates were plausible.")
                    coalescing_reason = "ambiguous"
                add_log("info", "broker", (
                    f"startup_coalescing origin={_masked_identity(origin_identity_hash)} "
                    f"window_seconds={startup_coalescing_window_seconds} "
                    f"coalescing_candidate_count={len(coalescing_candidates)} result={coalescing_reason}"
                ))
            elif identity_type in {"explicit_session", "stable_client_id"}:
                add_log("info", "broker", (
                    f"startup_coalescing origin={_masked_identity(origin_identity_hash)} "
                    f"window_seconds={startup_coalescing_window_seconds} coalescing_candidate_count=0 "
                    f"result={'explicit_session_conflict' if identity_type == 'explicit_session' else 'stable_identity_no_match'}"
                ))
        superseded = None
        if lifecycle_enabled and bool(policy["supersession"]):
            superseded, supersession_reason = _supersession_candidate(
                conn, catalog_item_id=catalog_item_id, media_type=media_type,
                identity_type=identity_type, identity=identity)
            if superseded is not None:
                now_switch = _now().isoformat()
                conn.execute("""UPDATE broker_reservations SET status='superseded', lifecycle_state='superseded',
                    superseded_at=?, release_reason='same_identity_content_switch',
                    last_action='reservation_superseded' WHERE reservation_id=?""",
                    (now_switch, superseded["reservation_id"]))
                conn.execute("UPDATE broker_reservation_identity_aliases SET active=0 WHERE reservation_id=?",
                             (superseded["reservation_id"],))
            elif supersession_reason not in {"no_match", "identity_not_high_confidence"}:
                add_log("info", "broker", f"supersession_skipped catalog_item={catalog_item_id} identity_type={identity_type} reason={supersession_reason}")
        active_counts = _active_counts(conn)
        eligible: list[tuple[tuple, sqlite3.Row, int]] = []
        disabled_count = unhealthy_count = capacity_count = 0
        for row in rows:
            active_count = active_counts.get(row["account_id"], 0) if row["account_id"] else 0
            if not bool(row["enabled"]) or not row["provider_id"] or not row["account_id"]:
                disabled_count += 1
                evaluated_candidates.append(_candidate_from_row(row, active_count, "account disabled", "Source is disabled or not linked to a provider/account."))
                continue
            if not bool(row["provider_enabled"]) or not bool(row["account_enabled"]):
                disabled_count += 1
                reason = "provider disabled" if not bool(row["provider_enabled"]) else "account disabled"
                detail = "Provider is disabled." if reason == "provider disabled" else "Account is disabled."
                evaluated_candidates.append(_candidate_from_row(row, active_count, reason, detail))
                continue
            if row["provider_health_status"] in BLOCKED_HEALTH_STATUSES or row["account_health_status"] in BLOCKED_HEALTH_STATUSES:
                unhealthy_count += 1
                evaluated_candidates.append(_candidate_from_row(row, active_count, "unhealthy", "Provider or account health status is unavailable for broker use."))
                continue
            max_streams = max(int(row["max_simultaneous_streams"] or 1), 1)
            if active_count >= max_streams:
                capacity_count += 1
                evaluated_candidates.append(_candidate_from_row(row, active_count, "at capacity", f"Account usage is {active_count} / {max_streams}."))
                continue
            priority_rank = PRIORITY_ORDER.get(row["priority_group"], 99)
            sort_key = (
                priority_rank,
                -int(row["weight"] or 0),
                active_count,
                str(row["provider_name"] or "").lower(),
                str(row["account_name"] or "").lower(),
                int(row["id"]),
            )
            eligible.append((sort_key, row, active_count))
        decision_reasons.append(f"Excluded {disabled_count} disabled/unassigned source(s).")
        decision_reasons.append(f"Excluded {unhealthy_count} unavailable health source(s).")
        decision_reasons.append(f"Excluded {capacity_count} source(s) at account capacity.")
        if not eligible:
            raise _classify_no_source(rows, decision_reasons, evaluated_candidates)
        eligible.sort(key=lambda item: item[0])
        _, selected, selected_active_count = eligible[0]
        for index, (_, row, active_count) in enumerate(eligible):
            if index == 0:
                evaluated_candidates.append(
                    _candidate_from_row(
                        row,
                        active_count,
                        "selected",
                        "Best candidate by priority group, weight, active reservation count, and stable source id.",
                        selected=True,
                    )
                )
            else:
                evaluated_candidates.append(
                    _candidate_from_row(
                        row,
                        active_count,
                        "lower weight",
                        "Eligible but ranked below the selected source by broker policy.",
                    )
                )
        decision_reasons.append(
            "Selected by priority group, weight, active reservation count, then stable source id."
        )
        now = _now()
        provisional_ttl = int(policy["provisional_ttl"])
        provisional_expires_at = now + timedelta(seconds=provisional_ttl)
        active_expires_at = now + timedelta(seconds=ttl)
        expires_at = provisional_expires_at if lifecycle_enabled else active_expires_at
        reservation_id = uuid4().hex
        if reserve:
            try:
                conn.execute(
                    """
                    INSERT INTO broker_reservations (
                        reservation_id, catalog_item_id, source_availability_id, provider_id, account_id,
                        media_type, location_ref, status, created_at, expires_at, released_at, client_label,
                        client_session, client_fingerprint, identity_type, last_seen_at, last_action,
                        playback_identity_key, stable_client_id, origin_identity_hash, request_profile
                        , lifecycle_state, provisional_expires_at, active_expires_at, first_seen_at,
                        request_count, distinct_activity_count, active_ttl_seconds, provisional_hard_expires_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, 'reservation_created', ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, ?, ?)
                    """,
                    (
                        reservation_id, selected["catalog_internal_id"], selected["id"], selected["provider_id"],
                        selected["account_id"], selected["media_type"], selected["location_ref"],
                        "provisional" if lifecycle_enabled else "active", now.isoformat(),
                        expires_at.isoformat(), client_label or None, client_session or None,
                        client_fingerprint or None, identity_type, now.isoformat(), playback_identity_key,
                        stable_client_id, origin_identity_hash, request_profile,
                        "provisional" if lifecycle_enabled else "active",
                        provisional_expires_at.isoformat() if lifecycle_enabled else None,
                        None if lifecycle_enabled else active_expires_at.isoformat(), now.isoformat(), ttl,
                        (now + timedelta(seconds=provisional_ttl * 2)).isoformat() if lifecycle_enabled else None,
                    ),
                )
                if identity_type and identity:
                    _register_identity_alias(conn, reservation_id=reservation_id, identity_type=identity_type,
                        identity_hash=identity, origin_identity_hash=origin_identity_hash,
                        request_profile=request_profile, seen_at=now.isoformat())
            except sqlite3.IntegrityError:
                winner = conn.execute("""SELECT * FROM broker_reservations
                    WHERE lifecycle_state IN ('provisional','active') AND playback_identity_key=? LIMIT 1""",
                    (playback_identity_key,)).fetchone()
                if winner is None:
                    raise
                return _reused_decision(
                    conn, reusable=winner, ttl=ttl, decision_reasons=decision_reasons,
                    evaluated_candidates=evaluated_candidates,
                    reuse_reason="Reused the reservation that won a concurrent playback acquisition.",
                    reuse_action="reservation_reused_after_race",
                )
            if superseded is not None:
                conn.execute("UPDATE broker_reservations SET superseded_by_reservation_id=? WHERE reservation_id=?",
                             (reservation_id, superseded["reservation_id"]))
            conn.commit()
            if superseded is not None:
                add_log("info", "broker", f"reservation_superseded old_reservation={superseded['reservation_id']} new_reservation={reservation_id} identity_type={identity_type} old_catalog={superseded['catalog_item_id']} new_catalog={catalog_item_id} reason=same_identity_content_switch")
            reservation_row = _reservation_detail_row(conn, reservation_id)
        else:
            reservation_row = {
                "reservation_id": reservation_id,
                "catalog_item_id": selected["catalog_internal_id"],
                "catalog_title": selected["catalog_title"],
                "source_availability_id": selected["id"],
                "provider_id": selected["provider_id"],
                "provider_name": selected["provider_name"],
                "account_id": selected["account_id"],
                "account_name": selected["account_name"],
                "media_type": selected["media_type"],
                "location_ref": selected["location_ref"],
                "status": "released", "lifecycle_state": "released",
                "created_at": now.isoformat(),
                "expires_at": expires_at.isoformat(),
                "released_at": now.isoformat(),
                "client_label": client_label or None,
                "client_session": client_session or None,
                "client_fingerprint": client_fingerprint or None,
                "stable_client_id": stable_client_id,
                "identity_type": identity_type,
                "last_seen_at": now.isoformat(),
                "last_action": "reservation_probe",
                "duplicate_warning": 0,
                "first_seen_at": now.isoformat(), "request_count": 1, "distinct_activity_count": 0,
            }
    selection = _source_selection_from_row(selected, selected_active_count)
    reservation = _reservation_from_row(reservation_row)
    if reserve:
        event = "reservation_provisional_created" if lifecycle_enabled else "reservation_created"
        add_log("info", "broker", f"{event} reservation={reservation.reservation_id} identity_type={identity_type} identity={_masked_identity(identity)} catalog_item={selection.catalog_item_id} account={selection.account_name}")
    return BrokerDecision(
        selected_source=selection,
        reservation=reservation,
        stream_url=selection.location_ref,
        expires_at=reservation.expires_at,
        reservation_ttl_seconds=ttl,
        decision_reason="Selected by priority group, weight, active reservation count, then stable source id.",
        decision_reasons=decision_reasons,
        evaluated_candidates=evaluated_candidates,
        reservation_created=reserve,
        reservation_reused=False,
        reuse_reason=None,
    )


def confirm_reservation(reservation_id: str) -> BrokerReservation | None:
    with closing(_connect()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        expire_reservations(conn, commit=False)
        row = conn.execute("SELECT * FROM broker_reservations WHERE reservation_id=?", (reservation_id,)).fetchone()
        if row is None:
            return None
        if row["lifecycle_state"] not in CONSUMING_STATES:
            raise ValueError(f"Reservation cannot be confirmed from {row['lifecycle_state']} state")
        if row["lifecycle_state"] == "provisional":
            now = _now()
            ttl = int(row["active_ttl_seconds"] or _lease_policy(row["media_type"])["active_ttl"])
            conn.execute("""UPDATE broker_reservations SET lifecycle_state='active', status='active', promoted_at=?,
                active_expires_at=?, expires_at=?, promotion_reason='explicit_confirmation',
                last_action='reservation_promoted', last_seen_at=? WHERE reservation_id=?""",
                (now.isoformat(), (now + timedelta(seconds=ttl)).isoformat(),
                 (now + timedelta(seconds=ttl)).isoformat(), now.isoformat(), reservation_id))
        conn.commit()
    result = _reservation_query("WHERE broker_reservations.reservation_id=?", (reservation_id,))
    if result:
        add_log("info", "broker", f"reservation_promoted reservation={reservation_id} reason=explicit_confirmation")
    return result[0] if result else None


def heartbeat_reservation(reservation_id: str) -> BrokerReservation | None:
    with closing(_connect()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        expire_reservations(conn, commit=False)
        row = conn.execute("SELECT * FROM broker_reservations WHERE reservation_id=?", (reservation_id,)).fetchone()
        if row is None:
            return None
        if row["lifecycle_state"] not in CONSUMING_STATES:
            conn.commit()
            return _reservation_from_row(_reservation_detail_row(conn, reservation_id))
        now = _now()
        policy = _lease_policy(row["media_type"], row["active_ttl_seconds"])
        expiry = (now + timedelta(seconds=int(policy["active_ttl"]))).isoformat()
        conn.execute("""UPDATE broker_reservations SET last_seen_at=?, request_count=COALESCE(request_count,1)+1,
            distinct_activity_count=COALESCE(distinct_activity_count,0)+1, last_action='reservation_heartbeat',
            active_expires_at=CASE WHEN lifecycle_state='active' AND ? THEN ? ELSE active_expires_at END,
            expires_at=CASE WHEN lifecycle_state='active' AND ? THEN ? ELSE expires_at END
            WHERE reservation_id=?""", (now.isoformat(), bool(policy["sliding"]), expiry,
            bool(policy["sliding"]), expiry, reservation_id))
        conn.commit()
    add_log("info", "broker", f"reservation_heartbeat reservation={reservation_id}")
    result = _reservation_query("WHERE broker_reservations.reservation_id=?", (reservation_id,))
    return result[0] if result else None


def force_expire_reservation(reservation_id: str) -> BrokerReservation | None:
    with closing(_connect()) as conn:
        row = conn.execute("SELECT lifecycle_state FROM broker_reservations WHERE reservation_id=?", (reservation_id,)).fetchone()
        if row is None:
            return None
        if row["lifecycle_state"] in CONSUMING_STATES:
            now = _now().isoformat()
            conn.execute("""UPDATE broker_reservations SET lifecycle_state='expired', status='expired',
                release_reason='manual_expire', last_action='reservation_expired', expires_at=?
                WHERE reservation_id=?""", (now, reservation_id))
            conn.execute("UPDATE broker_reservation_identity_aliases SET active=0 WHERE reservation_id=?", (reservation_id,))
            conn.commit()
    add_log("info", "broker", f"reservation_expired reservation={reservation_id} reason=manual_expire")
    result = _reservation_query("WHERE broker_reservations.reservation_id=?", (reservation_id,))
    return result[0] if result else None


def release_reservation(reservation_id: str, reason: str = "explicit_release") -> BrokerReservation | None:
    with closing(_connect()) as conn:
        expire_reservations(conn)
        now = _now().isoformat()
        existing = conn.execute("SELECT * FROM broker_reservations WHERE reservation_id = ?", (reservation_id,)).fetchone()
        if existing is None:
            return None
        if existing["lifecycle_state"] in CONSUMING_STATES:
            conn.execute(
                """UPDATE broker_reservations SET status='released', lifecycle_state='released',
                    released_at=?, release_reason=?, last_action='reservation_released' WHERE reservation_id=?""",
                (now, reason, reservation_id),
            )
            conn.execute("UPDATE broker_reservation_identity_aliases SET active=0 WHERE reservation_id=?",
                         (reservation_id,))
            conn.commit()
    add_log("info", "broker", f"reservation_released reservation={reservation_id} reason={reason}")
    rows = _reservation_query("WHERE broker_reservations.reservation_id = ?", (reservation_id,))
    return rows[0] if rows else None


def release_all_active() -> BrokerStatus:
    with closing(_connect()) as conn:
        expire_reservations(conn)
        now = _now().isoformat()
        result = conn.execute(
            """UPDATE broker_reservations SET status='released', lifecycle_state='released', released_at=?,
                release_reason='release_all_consuming', last_action='reservation_released'
                WHERE lifecycle_state IN ('provisional','active')""",
            (now,),
        )
        conn.execute("UPDATE broker_reservation_identity_aliases SET active=0 WHERE active=1")
        conn.commit()
    add_log("info", "broker", f"Released {result.rowcount} capacity-consuming broker reservation(s)")
    return get_status()


def repair_duplicate_reservations() -> DuplicateRepairResult:
    result = DuplicateRepairResult()
    with closing(_connect()) as conn:
        expire_reservations(conn)
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute("""SELECT * FROM broker_reservations WHERE lifecycle_state IN ('provisional','active')
            AND (client_session IS NOT NULL OR client_fingerprint IS NOT NULL)
            ORDER BY created_at""").fetchall()
        groups: dict[tuple, list[sqlite3.Row]] = {}
        for row in rows:
            identity_type = "explicit_session" if row["client_session"] else "derived_fingerprint"
            identity = row["client_session"] or row["client_fingerprint"]
            groups.setdefault((row["catalog_item_id"], row["media_type"], identity_type, identity), []).append(row)
        now = _now().isoformat()
        for duplicates in groups.values():
            if len(duplicates) < 2:
                continue
            reused = [row for row in duplicates if int(row["reuse_count"] or 0) > 0 or row["last_reused_at"]]
            keep = max(reused, key=lambda row: row["last_seen_at"] or row["last_reused_at"] or row["created_at"]) if reused else min(duplicates, key=lambda row: row["created_at"])
            redundant_ids = [row["reservation_id"] for row in duplicates if row["reservation_id"] != keep["reservation_id"]]
            placeholders = ",".join("?" for _ in redundant_ids)
            conn.execute(f"UPDATE broker_reservations SET status='released', lifecycle_state='released', released_at=?, release_reason='duplicate_repair', last_action='duplicate_released' WHERE reservation_id IN ({placeholders})", (now, *redundant_ids))
            conn.execute(f"UPDATE broker_reservation_identity_aliases SET active=0 WHERE reservation_id IN ({placeholders})",
                         tuple(redundant_ids))
            result.duplicate_groups += 1
            result.released_reservations += len(redundant_ids)
            result.kept_reservation_ids.append(keep["reservation_id"])
        conn.commit()
    add_log("info", "broker", f"Duplicate reservation repair completed: groups={result.duplicate_groups}, released={result.released_reservations}")
    return result


def expire_now() -> BrokerStatus:
    with closing(_connect()) as conn:
        expire_reservations(conn)
    return get_status()


def get_status() -> BrokerStatus:
    with closing(_connect()) as conn:
        expire_reservations(conn)
        counts = {
            row["lifecycle_state"]: row["count"]
            for row in conn.execute("SELECT lifecycle_state, COUNT(*) AS count FROM broker_reservations GROUP BY lifecycle_state").fetchall()
        }
        total_reservations = conn.execute("SELECT COUNT(*) AS count FROM broker_reservations").fetchone()["count"]
        active_counts = _active_counts(conn)
        state_counts = {(row["account_id"], row["lifecycle_state"]): row["count"] for row in conn.execute(
            """SELECT account_id,lifecycle_state,COUNT(*) count FROM broker_reservations
               WHERE lifecycle_state IN ('provisional','active') GROUP BY account_id,lifecycle_state""").fetchall()}
        account_rows = conn.execute(
            """
            SELECT
                accounts.*,
                providers.friendly_name AS provider_name,
                providers.enabled AS provider_enabled
            FROM accounts
            LEFT JOIN providers ON providers.id = accounts.provider_id
            ORDER BY providers.friendly_name, accounts.priority_group, accounts.weight DESC, accounts.friendly_name
            """
        ).fetchall()
    usage: list[BrokerAccountUsage] = []
    for row in account_rows:
        active_count = active_counts.get(row["id"], 0)
        provisional_count = state_counts.get((row["id"], "provisional"), 0)
        confirmed_active_count = state_counts.get((row["id"], "active"), 0)
        max_streams = max(int(row["max_simultaneous_streams"] or 1), 1)
        enabled = bool(row["enabled"]) and bool(row["provider_enabled"])
        health_available = row["health_status"] not in BLOCKED_HEALTH_STATUSES
        usage.append(
            BrokerAccountUsage(
                account_id=row["id"],
                account_name=row["friendly_name"],
                provider_id=row["provider_id"],
                provider_name=row["provider_name"] or "Unknown provider",
                enabled=enabled,
                health_status=row["health_status"],
                priority_group=row["priority_group"],
                weight=row["weight"],
                active_reservations=confirmed_active_count,
                provisional_reservations=provisional_count,
                consuming_reservations=active_count,
                max_simultaneous_streams=max_streams,
                at_capacity=active_count >= max_streams,
                available=enabled and health_available and active_count < max_streams,
            )
        )
    return BrokerStatus(
        total_reservations=total_reservations,
        active_reservations=counts.get("active", 0),
        provisional_reservations=counts.get("provisional", 0),
        superseded_reservations=counts.get("superseded", 0),
        consuming_reservations=counts.get("active", 0) + counts.get("provisional", 0),
        released_reservations=counts.get("released", 0),
        expired_reservations=counts.get("expired", 0),
        failed_reservations=counts.get("failed", 0),
        accounts_at_capacity=sum(1 for item in usage if item.at_capacity),
        available_accounts=sum(1 for item in usage if item.available),
        account_usage=usage,
    )
