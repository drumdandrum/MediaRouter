from __future__ import annotations

from datetime import datetime, timedelta
import os
from pathlib import Path
import re
import sqlite3
from uuid import uuid4

from app.core.config import get_settings
from app.schemas.broker import (
    BrokerAccountUsage,
    BrokerDecision,
    BrokerErrorDetail,
    BrokerEvaluatedCandidate,
    BrokerReservation,
    BrokerSourceSelection,
    BrokerStatus,
)
from app.services.logs import add_log


DEFAULT_RESERVATION_TTL_SECONDS = 60
DEFAULT_REUSE_WINDOW_SECONDS = int(os.getenv("MEDIA_ROUTER_RESERVATION_REUSE_WINDOW_SECONDS", "30"))
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
    }
    applied: list[str] = []
    for column, statement in migrations.items():
        if column not in columns:
            conn.execute(statement)
            applied.append(column)
    conn.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_broker_reservations_status ON broker_reservations(status);
        CREATE INDEX IF NOT EXISTS idx_broker_reservations_account ON broker_reservations(account_id, status);
        CREATE INDEX IF NOT EXISTS idx_broker_reservations_expires ON broker_reservations(expires_at);
        CREATE INDEX IF NOT EXISTS idx_broker_reservations_reuse_session ON broker_reservations(catalog_item_id, media_type, status, client_session, created_at);
        CREATE INDEX IF NOT EXISTS idx_broker_reservations_reuse_fingerprint ON broker_reservations(catalog_item_id, media_type, status, client_fingerprint, created_at);
        """
    )
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


def _redact_ref(value: str) -> str:
    return re.sub(r"(/(?:live|movie|series)/)[^/]+/[^/]+/", r"\1[redacted]/[redacted]/", value, flags=re.IGNORECASE)


def expire_reservations(conn: sqlite3.Connection | None = None) -> int:
    owns_conn = conn is None
    if conn is None:
        conn = _connect()
    now = _now().isoformat()
    result = conn.execute(
        """
        UPDATE broker_reservations
        SET status = 'expired'
        WHERE status = 'active'
          AND expires_at <= ?
        """,
        (now,),
    )
    conn.commit()
    expired = result.rowcount
    if owns_conn:
        conn.close()
    return expired


def _active_counts(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        """
        SELECT account_id, COUNT(*) AS active_count
        FROM broker_reservations
        WHERE status = 'active'
        GROUP BY account_id
        """
    ).fetchall()
    return {row["account_id"]: row["active_count"] for row in rows}


def _reservation_from_row(row: sqlite3.Row) -> BrokerReservation:
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
        status=row["status"],
        created_at=datetime.fromisoformat(row["created_at"]),
        expires_at=datetime.fromisoformat(row["expires_at"]),
        released_at=datetime.fromisoformat(row["released_at"]) if row["released_at"] else None,
        client_label=row["client_label"],
        client_session=row["client_session"] if "client_session" in row.keys() else None,
        last_reused_at=datetime.fromisoformat(row["last_reused_at"]) if "last_reused_at" in row.keys() and row["last_reused_at"] else None,
        reuse_count=int(row["reuse_count"] or 0) if "reuse_count" in row.keys() else 0,
    )


def _reservation_query(where: str = "", params: tuple = ()) -> list[BrokerReservation]:
    with _connect() as conn:
        expire_reservations(conn)
        rows = conn.execute(
            f"""
            SELECT
                broker_reservations.*,
                catalog_items.title AS catalog_title,
                providers.friendly_name AS provider_name,
                accounts.friendly_name AS account_name
            FROM broker_reservations
            LEFT JOIN catalog_items ON catalog_items.internal_id = broker_reservations.catalog_item_id
            LEFT JOIN providers ON providers.id = broker_reservations.provider_id
            LEFT JOIN accounts ON accounts.id = broker_reservations.account_id
            {where}
            ORDER BY
                CASE broker_reservations.status
                    WHEN 'active' THEN 0
                    WHEN 'expired' THEN 1
                    WHEN 'released' THEN 2
                    ELSE 3
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
    with _connect() as conn:
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
        FROM broker_reservations
        LEFT JOIN catalog_items ON catalog_items.internal_id = broker_reservations.catalog_item_id
        LEFT JOIN providers ON providers.id = broker_reservations.provider_id
        LEFT JOIN accounts ON accounts.id = broker_reservations.account_id
        WHERE broker_reservations.reservation_id = ?
        """,
        (reservation_id,),
    ).fetchone()


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
    cutoff = (_now() - timedelta(seconds=max(reuse_window_seconds, 1))).isoformat()
    if client_session:
        return conn.execute(
            """
            SELECT *
            FROM broker_reservations
            WHERE status = 'active'
              AND catalog_item_id = ?
              AND (? IS NULL OR media_type = ?)
              AND client_session = ?
              AND created_at >= ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (catalog_item_id, normalized_media_type, normalized_media_type, client_session, cutoff),
        ).fetchone()
    if client_fingerprint:
        return conn.execute(
            """
            SELECT *
            FROM broker_reservations
            WHERE status = 'active'
              AND catalog_item_id = ?
              AND (? IS NULL OR media_type = ?)
              AND client_fingerprint = ?
              AND created_at >= ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (catalog_item_id, normalized_media_type, normalized_media_type, client_fingerprint, cutoff),
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
    reservation_row = _reservation_detail_row(conn, reusable["reservation_id"])
    if reservation_row is None:
        raise BrokerUnavailable("no_sources", "Reusable reservation was not available.", decision_reasons, evaluated_candidates)
    conn.execute(
        """
        UPDATE broker_reservations
        SET last_reused_at = ?, reuse_count = COALESCE(reuse_count, 0) + 1
        WHERE reservation_id = ?
        """,
        (_now().isoformat(), reusable["reservation_id"]),
    )
    conn.commit()
    selection = _source_selection_from_row(selected_row, active_count)
    reservation = _reservation_from_row(reservation_row)
    decision_reasons.append(reuse_reason)
    add_log("info", "broker", f"Reused reservation {reservation.reservation_id} for {selection.catalog_item_id} on account {selection.account_name}")
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


def resolve_source(
    catalog_item_id: str,
    media_type: str | None = None,
    client_label: str | None = None,
    client_session: str | None = None,
    client_fingerprint: str | None = None,
    allow_reservation_reuse: bool = False,
    reuse_window_seconds: int = DEFAULT_REUSE_WINDOW_SECONDS,
    reserve: bool = True,
    reservation_ttl_seconds: int | None = None,
) -> BrokerDecision:
    ttl = reservation_ttl_seconds or DEFAULT_RESERVATION_TTL_SECONDS
    decision_reasons = [f"TTL set to {ttl} seconds.", "Expired reservations were ignored before selection."]
    with _connect() as conn:
        expire_reservations(conn)
        rows = _source_rows(conn, catalog_item_id, media_type)
        if media_type:
            decision_reasons.append(f"Filtered sources by media type {_normalize_media_type(media_type)}.")
        evaluated_candidates: list[BrokerEvaluatedCandidate] = []
        if not rows:
            raise _classify_no_source(rows, decision_reasons, evaluated_candidates)
        decision_reasons.append(f"Found {len(rows)} source record(s) for the catalog item.")
        if allow_reservation_reuse:
            reusable = _find_reusable_reservation(
                conn,
                catalog_item_id=catalog_item_id,
                media_type=media_type,
                client_session=client_session,
                client_fingerprint=client_fingerprint,
                reuse_window_seconds=reuse_window_seconds,
            )
            if reusable is not None:
                identity_reason = "client_session" if client_session else "short-lived client fingerprint"
                return _reused_decision(
                    conn,
                    reusable=reusable,
                    ttl=ttl,
                    decision_reasons=decision_reasons,
                    evaluated_candidates=evaluated_candidates,
                    reuse_reason=f"Reused active reservation within {reuse_window_seconds}s by {identity_reason}.",
                )
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
        expires_at = now + timedelta(seconds=ttl)
        reservation_id = uuid4().hex
        if reserve:
            conn.execute(
                """
                INSERT INTO broker_reservations (
                    reservation_id, catalog_item_id, source_availability_id, provider_id, account_id,
                    media_type, location_ref, status, created_at, expires_at, released_at, client_label,
                    client_session, client_fingerprint
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, NULL, ?, ?, ?)
                """,
                (
                    reservation_id,
                    selected["catalog_internal_id"],
                    selected["id"],
                    selected["provider_id"],
                    selected["account_id"],
                    selected["media_type"],
                    selected["location_ref"],
                    now.isoformat(),
                    expires_at.isoformat(),
                    client_label or None,
                    client_session or None,
                    client_fingerprint or None,
                ),
            )
            conn.commit()
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
                "status": "released",
                "created_at": now.isoformat(),
                "expires_at": expires_at.isoformat(),
                "released_at": now.isoformat(),
                "client_label": client_label or None,
                "client_session": client_session or None,
            }
    selection = _source_selection_from_row(selected, selected_active_count)
    reservation = _reservation_from_row(reservation_row)
    if reserve:
        add_log("info", "broker", f"Reserved {selection.catalog_item_id} on account {selection.account_name}")
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


def release_reservation(reservation_id: str) -> BrokerReservation | None:
    with _connect() as conn:
        expire_reservations(conn)
        now = _now().isoformat()
        existing = conn.execute("SELECT * FROM broker_reservations WHERE reservation_id = ?", (reservation_id,)).fetchone()
        if existing is None:
            return None
        if existing["status"] == "active":
            conn.execute(
                "UPDATE broker_reservations SET status = 'released', released_at = ? WHERE reservation_id = ?",
                (now, reservation_id),
            )
            conn.commit()
    add_log("info", "broker", f"Released reservation {reservation_id}")
    rows = _reservation_query("WHERE broker_reservations.reservation_id = ?", (reservation_id,))
    return rows[0] if rows else None


def release_all_active() -> BrokerStatus:
    with _connect() as conn:
        expire_reservations(conn)
        now = _now().isoformat()
        result = conn.execute(
            "UPDATE broker_reservations SET status = 'released', released_at = ? WHERE status = 'active'",
            (now,),
        )
        conn.commit()
    add_log("info", "broker", f"Released {result.rowcount} active broker reservation(s)")
    return get_status()


def expire_now() -> BrokerStatus:
    with _connect() as conn:
        expire_reservations(conn)
    return get_status()


def get_status() -> BrokerStatus:
    with _connect() as conn:
        expire_reservations(conn)
        counts = {
            row["status"]: row["count"]
            for row in conn.execute("SELECT status, COUNT(*) AS count FROM broker_reservations GROUP BY status").fetchall()
        }
        total_reservations = conn.execute("SELECT COUNT(*) AS count FROM broker_reservations").fetchone()["count"]
        active_counts = _active_counts(conn)
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
                active_reservations=active_count,
                max_simultaneous_streams=max_streams,
                at_capacity=active_count >= max_streams,
                available=enabled and health_available and active_count < max_streams,
            )
        )
    return BrokerStatus(
        total_reservations=total_reservations,
        active_reservations=counts.get("active", 0),
        released_reservations=counts.get("released", 0),
        expired_reservations=counts.get("expired", 0),
        failed_reservations=counts.get("failed", 0),
        accounts_at_capacity=sum(1 for item in usage if item.at_capacity),
        available_accounts=sum(1 for item in usage if item.available),
        account_usage=usage,
    )
