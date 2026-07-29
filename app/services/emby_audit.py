from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime
from html import unescape
import re
import sqlite3
import unicodedata
from typing import Any
from urllib.parse import urlencode

from app.schemas.integrations import (
    EmbyMappingAuditCollisionTotals,
    EmbyMappingAuditItem,
    EmbyMappingAuditRequest,
    EmbyMappingAuditResponse,
)
from app.services import emby


EMBY_AUDIT_PAGE_SIZE = 200
EMBY_AUDIT_SCAN_CAP = 10_000
AUDIT_MEDIA_TYPES = ("channel", "movie", "series", "episode")
AUDIT_CLASSIFICATIONS = (
    "manual", "exact", "normalized_title", "placement_title",
    "ambiguous", "unmatched", "unsupported",
)
AUDIT_EVIDENCE_SOURCES = (
    "manual_mapping", "durable_marker", "catalog_external_id",
    "persisted_item_id", "persisted_media_source_id",
    "canonical_normalized_title", "active_placement_title",
    "structural_episode_identity",
)
EMBY_ITEM_TYPES = {"movie": "Movie", "series": "Series", "episode": "Episode"}
EMBY_TYPE_MAP = {
    "tvchannel": "channel",
    "livetvchannel": "channel",
    "channel": "channel",
    "movie": "movie",
    "series": "series",
    "episode": "episode",
}


def _normalize(value: Any) -> str:
    text = unicodedata.normalize("NFKC", unescape(str(value or "")))
    return re.sub(r"\s+", " ", text).strip().casefold()


def _safe_text(value: Any, limit: int = 256) -> str:
    text = str(value or "")[:4096]
    while True:
        decoded = unescape(text)
        if decoded == text:
            break
        text = decoded
    text = re.sub(
        r"\b[a-z][a-z0-9+.-]*://\S+",
        "[redacted-url]",
        text,
        flags=re.I,
    )
    text = re.sub(
        r"\b(?:https?|rtsp|rtmp|smb|file|ftp|udp|plugin):\S+",
        "[redacted-url]",
        text,
        flags=re.I,
    )
    text = re.sub(
        r"(?<![A-Za-z0-9+.-])[a-z][a-z0-9+.-]*:"
        r"(?=\S*[/\\?#[\]@%=&_.])\S+",
        "[redacted-url]",
        text,
    )
    text = re.sub(
        r"(?<![A-Za-z0-9])(?:/[^\s/]\S*|[A-Za-z]:[\\/]\S+|\\\\\S+)",
        "[redacted-path]",
        text,
    )
    text = re.sub(
        r"(?i)\b(api[_-]?key|access[_-]?token|token|password|secret)"
        r"\s*([=:])\s*\S+",
        r"\1\2[redacted]",
        text,
    )
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _safe_number(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if 0 <= number <= 100_000 else None


def _audit_media_type(item: dict[str, Any]) -> str | None:
    return EMBY_TYPE_MAP.get(str(item.get("Type") or "").strip().casefold())


def _read_only_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{emby._db_path()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _page_items(payload: Any) -> tuple[list[dict[str, Any]], int | None]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)], None
    if not isinstance(payload, dict) or not isinstance(payload.get("Items"), list):
        raise emby.EmbyError("Emby returned an unexpected library response.", "error")
    items = [item for item in payload["Items"] if isinstance(item, dict)]
    total = payload.get("TotalRecordCount")
    return items, int(total) if isinstance(total, int) and total >= 0 else None


def _scan_endpoint(
    settings: dict[str, Any],
    path: str,
    fixed_query: dict[str, str],
    remaining: int,
) -> tuple[list[dict[str, Any]], bool]:
    scanned: list[dict[str, Any]] = []
    start = 0
    complete = True
    while remaining > 0:
        page_limit = min(EMBY_AUDIT_PAGE_SIZE, remaining)
        query = urlencode({**fixed_query, "StartIndex": start, "Limit": page_limit})
        page, total = _page_items(emby._request_json(f"{path}?{query}", settings))
        if len(page) > page_limit:
            page = page[:page_limit]
            complete = False
        scanned.extend(page)
        start += len(page)
        remaining -= len(page)
        if total is not None and start >= total:
            break
        if len(page) < page_limit:
            break
        if remaining == 0:
            complete = total is not None and start >= total
            break
    return scanned, complete


def _scan_library(
    settings: dict[str, Any],
    requested: set[str],
) -> tuple[list[dict[str, Any]], bool]:
    scanned: list[dict[str, Any]] = []
    complete = True
    if "channel" in requested:
        channel_items, channel_complete = _scan_endpoint(
            settings,
            "/LiveTv/Channels",
            {"Fields": "Path,ProviderIds,MediaSources,Tags"},
            EMBY_AUDIT_SCAN_CAP - len(scanned),
        )
        scanned.extend(channel_items)
        complete = complete and channel_complete
    vod_types = [EMBY_ITEM_TYPES[media_type] for media_type in AUDIT_MEDIA_TYPES
                 if media_type in requested and media_type in EMBY_ITEM_TYPES]
    if vod_types and len(scanned) < EMBY_AUDIT_SCAN_CAP:
        vod_items, vod_complete = _scan_endpoint(
            settings,
            "/Items",
            {
                "Recursive": "true",
                "IncludeItemTypes": ",".join(vod_types),
                "Fields": (
                    "ProviderIds,MediaSources,Path,SeriesName,ParentIndexNumber,"
                    "IndexNumber,SeriesId,ParentId"
                ),
            },
            EMBY_AUDIT_SCAN_CAP - len(scanned),
        )
        scanned.extend(vod_items)
        complete = complete and vod_complete
    elif vod_types:
        complete = False

    filtered: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in scanned:
        media_type = _audit_media_type(item)
        if media_type not in requested:
            continue
        item_id = emby._safe_id(item.get("Id"))
        key = (media_type, item_id or f"invalid:{len(filtered)}")
        if key in seen:
            continue
        seen.add(key)
        filtered.append(item)
    return filtered, complete


def _add_identity(
    index: dict[tuple[str, str], set[str]],
    media_type: str,
    value: Any,
    catalog_id: str,
) -> None:
    safe = emby._safe_id(value)
    if safe:
        index[(media_type, safe)].add(catalog_id)


def _catalog_context(
    conn: sqlite3.Connection,
    requested: set[str],
    server_id: str,
) -> dict[str, Any]:
    placeholders = ",".join("?" for _ in requested)
    rows = conn.execute(
        f"""SELECT internal_id,media_type,title,normalized_title,cuid,tvg_id,show_name,
                   season_number,episode_number,parent_internal_id
            FROM catalog_items WHERE media_type IN ({placeholders})""",
        tuple(sorted(requested)),
    ).fetchall()
    titles: dict[tuple[str, str], set[str]] = defaultdict(set)
    identities: dict[tuple[str, str], set[str]] = defaultdict(set)
    series_titles: dict[str, str] = {}
    for row in rows:
        catalog_id, media_type = row["internal_id"], row["media_type"]
        titles[(media_type, _normalize(row["title"]))].add(catalog_id)
        for value in (catalog_id, row["cuid"], row["tvg_id"]):
            _add_identity(identities, media_type, value, catalog_id)
        if media_type == "series":
            series_titles[catalog_id] = _normalize(row["title"])
    source_rows = conn.execute(
        f"""SELECT items.internal_id,items.media_type,sources.external_id
            FROM catalog_items items
            JOIN source_availability sources ON sources.catalog_internal_id=items.internal_id
            WHERE items.media_type IN ({placeholders})""",
        tuple(sorted(requested)),
    ).fetchall()
    for row in source_rows:
        _add_identity(
            identities, row["media_type"], row["external_id"], row["internal_id"],
        )

    episode_structures: dict[tuple[str, int, int], set[str]] = defaultdict(set)
    for row in rows:
        if row["media_type"] != "episode":
            continue
        series_name = _normalize(row["show_name"])
        if not series_name and row["parent_internal_id"]:
            series_name = series_titles.get(row["parent_internal_id"], "")
        season, episode_number = row["season_number"], row["episode_number"]
        if series_name and season is not None and episode_number is not None:
            episode_structures[(series_name, season, episode_number)].add(
                row["internal_id"],
            )

    placements: dict[str, set[str]] = defaultdict(set)
    if "channel" in requested:
        for row in conn.execute(
            "SELECT catalog_item_id,display_title FROM channel_placements WHERE active=1"
        ).fetchall():
            title = _normalize(row["display_title"])
            if title:
                placements[title].add(row["catalog_item_id"])

    channel_rows: list[sqlite3.Row] = []
    if "channel" in requested:
        channel_rows = conn.execute(
            """SELECT emby_item_id,emby_media_source_id,catalog_item_id,mapping_source
               FROM emby_channel_mappings
               WHERE integration_id=? AND catalog_item_id IS NOT NULL""",
            (server_id,),
        ).fetchall()
    return {
        "titles": titles,
        "identities": identities,
        "episode_structures": episode_structures,
        "placements": placements,
        "channel_rows": channel_rows,
    }


def _item_identity_values(item: dict[str, Any]) -> tuple[set[str], set[str], set[str]]:
    marker_values: set[str] = set()
    external_values: set[str] = set()
    media_source_ids: set[str] = set()
    runtime_id, _, _ = emby._runtime_identity(
        {"NowPlayingItem": item, "PlayState": {}}
    )
    if runtime_id:
        marker_values.add(runtime_id)
    provider_ids = item.get("ProviderIds")
    if isinstance(provider_ids, dict):
        for value in provider_ids.values():
            safe = emby._safe_id(value)
            if not safe:
                continue
            if safe.casefold().startswith("mr:"):
                marker_values.add(safe[3:])
            else:
                external_values.add(safe)
    tags = item.get("Tags") if isinstance(item.get("Tags"), list) else []
    for value in tags:
        safe = emby._safe_id(value)
        if safe and safe.casefold().startswith("mr:"):
            marker_values.add(safe[3:])
    item_id = emby._safe_id(item.get("Id"))
    if item_id:
        external_values.add(item_id)
    media_sources = item.get("MediaSources")
    if isinstance(media_sources, list):
        for source in media_sources:
            if not isinstance(source, dict):
                continue
            source_id = emby._safe_id(source.get("Id"))
            if source_id:
                media_source_ids.add(source_id)
                external_values.add(source_id)
    direct_source = emby._safe_id(item.get("MediaSourceId"))
    if direct_source:
        media_source_ids.add(direct_source)
        external_values.add(direct_source)
    return marker_values, external_values, media_source_ids


def _mapping_candidates(
    channel_rows: list[sqlite3.Row],
    item_id: str | None,
    media_source_ids: set[str],
) -> tuple[set[str], set[str], set[str]]:
    manual: set[str] = set()
    persisted_item: set[str] = set()
    persisted_source: set[str] = set()
    for row in channel_rows:
        item_match = bool(item_id and row["emby_item_id"] == item_id)
        source_match = bool(
            row["emby_media_source_id"]
            and row["emby_media_source_id"] in media_source_ids
        )
        if not item_match and not source_match:
            continue
        if row["mapping_source"] == "manual":
            manual.add(row["catalog_item_id"])
        elif item_match:
            persisted_item.add(row["catalog_item_id"])
        else:
            persisted_source.add(row["catalog_item_id"])
    return manual, persisted_item, persisted_source


def _apply_status(
    media_type: str,
    classification: str,
    evidence_source: str | None,
) -> tuple[bool, str | None]:
    if media_type != "channel":
        return False, "vod_application_not_supported"
    if classification in {"ambiguous", "unmatched", "unsupported"}:
        return False, f"{classification}_result"
    if evidence_source == "catalog_external_id":
        return False, "channel_workflow_does_not_apply_external_identity"
    return True, None


def _bounded_ids(values: set[str]) -> list[str]:
    return sorted(values)[:20]


def _classify_item(
    item: dict[str, Any],
    media_type: str,
    server_id: str,
    context: dict[str, Any],
    emby_name_counts: Counter[tuple[str, str]],
    emby_episode_counts: Counter[tuple[str, int, int]],
    collisions: EmbyMappingAuditCollisionTotals,
) -> EmbyMappingAuditItem:
    del server_id  # Server scoping is already applied while loading channel rows.
    name = _safe_text(item.get("Name")) or "Unnamed item"
    normalized = _normalize(name)
    item_id = emby._safe_id(item.get("Id"))
    series_name = _safe_text(item.get("SeriesName")) or None
    season_number = _safe_number(item.get("ParentIndexNumber"))
    episode_number = _safe_number(item.get("IndexNumber"))
    duplicate_name_count = emby_name_counts[(media_type, normalized)]
    title_candidates = set(context["titles"].get((media_type, normalized), set()))
    placement_candidates = (
        set(context["placements"].get(normalized, set()))
        if media_type == "channel" else set()
    )
    catalog_title_collision_count = (
        len(title_candidates) if len(title_candidates) > 1 else 0
    )
    placement_collision_count = (
        len(placement_candidates) if len(placement_candidates) > 1 else 0
    )
    if duplicate_name_count > 1:
        collisions.duplicate_emby_names += 1
    if catalog_title_collision_count > 1:
        collisions.duplicate_catalog_titles += 1
    if placement_collision_count > 1:
        collisions.placement_title_collisions += 1

    marker_values, external_values, media_source_ids = _item_identity_values(item)
    exact: dict[str, set[str]] = defaultdict(set)
    for value in marker_values:
        exact["durable_marker"].update(
            context["identities"].get((media_type, value), set())
        )
    for value in external_values:
        exact["catalog_external_id"].update(
            context["identities"].get((media_type, value), set())
        )

    manual: set[str] = set()
    if media_type == "channel":
        manual, persisted_item, persisted_source = _mapping_candidates(
            context["channel_rows"], item_id, media_source_ids,
        )
        if persisted_item:
            exact["persisted_item_id"].update(persisted_item)
        if persisted_source:
            exact["persisted_media_source_id"].update(persisted_source)

    exact_union = set().union(*exact.values()) if exact else set()
    if manual:
        manual_conflict = len(manual) > 1 or bool(exact_union - manual)
        if manual_conflict:
            collisions.conflicting_exact_evidence += 1
        catalog_id = next(iter(manual)) if len(manual) == 1 else None
        candidates = manual | exact_union
        classification = "manual" if catalog_id else "ambiguous"
        evidence = "manual_mapping" if catalog_id else None
        detail = (
            "Existing manual channel mapping takes precedence."
            if not manual_conflict else
            "Manual mapping is preserved, but other exact evidence conflicts."
        )
        apply_eligible, reason = _apply_status(media_type, classification, evidence)
        return EmbyMappingAuditItem(
            emby_item_id=item_id, item_name=name, media_type=media_type,
            series_name=series_name, season_number=season_number,
            episode_number=episode_number, catalog_item_id=catalog_id,
            classification=classification, evidence_source=evidence,
            candidate_count=len(candidates), distinct_candidate_ids=_bounded_ids(candidates),
            duplicate_emby_name_count=duplicate_name_count,
            catalog_title_collision_count=catalog_title_collision_count,
            placement_title_collision_count=placement_collision_count,
            apply_eligible=apply_eligible, ineligibility_reason=reason, detail=detail,
        )

    exact_conflict = (
        len(exact_union) > 1 or any(len(candidates) > 1 for candidates in exact.values())
    )
    if exact_conflict:
        collisions.conflicting_exact_evidence += 1
        return EmbyMappingAuditItem(
            emby_item_id=item_id, item_name=name, media_type=media_type,
            series_name=series_name, season_number=season_number,
            episode_number=episode_number, classification="ambiguous",
            candidate_count=len(exact_union),
            distinct_candidate_ids=_bounded_ids(exact_union),
            duplicate_emby_name_count=duplicate_name_count,
            catalog_title_collision_count=catalog_title_collision_count,
            placement_title_collision_count=placement_collision_count,
            ineligibility_reason="conflicting_exact_evidence",
            detail="Exact durable evidence resolves to multiple catalog items.",
        )
    if len(exact_union) == 1:
        evidence_order = (
            "durable_marker", "persisted_item_id",
            "persisted_media_source_id", "catalog_external_id",
        )
        evidence = next(
            label for label in evidence_order if exact.get(label)
        )
        catalog_id = next(iter(exact_union))
        apply_eligible, reason = _apply_status(media_type, "exact", evidence)
        return EmbyMappingAuditItem(
            emby_item_id=item_id, item_name=name, media_type=media_type,
            series_name=series_name, season_number=season_number,
            episode_number=episode_number, catalog_item_id=catalog_id,
            classification="exact", evidence_source=evidence,
            candidate_count=1, distinct_candidate_ids=[catalog_id],
            duplicate_emby_name_count=duplicate_name_count,
            catalog_title_collision_count=catalog_title_collision_count,
            placement_title_collision_count=placement_collision_count,
            apply_eligible=apply_eligible, ineligibility_reason=reason,
            detail="Consistent exact durable identity resolves one catalog item.",
        )

    if media_type == "episode":
        normalized_series = _normalize(series_name)
        if not normalized_series or season_number is None or episode_number is None:
            collisions.incomplete_episode_structure += 1
            return EmbyMappingAuditItem(
                emby_item_id=item_id, item_name=name, media_type=media_type,
                series_name=series_name, season_number=season_number,
                episode_number=episode_number, classification="unmatched",
                duplicate_emby_name_count=duplicate_name_count,
                catalog_title_collision_count=catalog_title_collision_count,
                ineligibility_reason="incomplete_episode_structure",
                detail="Episode title alone is insufficient; series, season, and episode are required.",
            )
        structure = (normalized_series, season_number, episode_number)
        candidates = set(context["episode_structures"].get(structure, set()))
        duplicate_structure = emby_episode_counts[structure]
        if duplicate_structure > 1 or len(candidates) > 1:
            collisions.duplicate_episode_structures += 1
            return EmbyMappingAuditItem(
                emby_item_id=item_id, item_name=name, media_type=media_type,
                series_name=series_name, season_number=season_number,
                episode_number=episode_number, classification="ambiguous",
                candidate_count=len(candidates),
                distinct_candidate_ids=_bounded_ids(candidates),
                duplicate_emby_name_count=duplicate_name_count,
                catalog_title_collision_count=len(candidates),
                ineligibility_reason="ambiguous_episode_structure",
                detail="Episode structure is duplicated in Emby or the catalog.",
            )
        if len(candidates) == 1:
            catalog_id = next(iter(candidates))
            return EmbyMappingAuditItem(
                emby_item_id=item_id, item_name=name, media_type=media_type,
                series_name=series_name, season_number=season_number,
                episode_number=episode_number, catalog_item_id=catalog_id,
                classification="exact", evidence_source="structural_episode_identity",
                candidate_count=1, distinct_candidate_ids=[catalog_id],
                duplicate_emby_name_count=duplicate_name_count,
                apply_eligible=False,
                ineligibility_reason="vod_application_not_supported",
                detail="Unique series, season, and episode structure resolves one catalog episode.",
            )
        return EmbyMappingAuditItem(
            emby_item_id=item_id, item_name=name, media_type=media_type,
            series_name=series_name, season_number=season_number,
            episode_number=episode_number, classification="unmatched",
            duplicate_emby_name_count=duplicate_name_count,
            ineligibility_reason="no_structural_episode_match",
            detail="No catalog episode has the same series, season, and episode structure.",
        )

    if normalized and duplicate_name_count == 1 and len(title_candidates) == 1:
        catalog_id = next(iter(title_candidates))
        apply_eligible, reason = _apply_status(
            media_type, "normalized_title", "canonical_normalized_title",
        )
        return EmbyMappingAuditItem(
            emby_item_id=item_id, item_name=name, media_type=media_type,
            catalog_item_id=catalog_id, classification="normalized_title",
            evidence_source="canonical_normalized_title", candidate_count=1,
            distinct_candidate_ids=[catalog_id],
            duplicate_emby_name_count=duplicate_name_count,
            catalog_title_collision_count=0,
            placement_title_collision_count=placement_collision_count,
            apply_eligible=apply_eligible, ineligibility_reason=reason,
            detail="Unique exact normalized title resolves one catalog item.",
        )
    if (
        media_type == "channel" and normalized and duplicate_name_count == 1
        and not title_candidates and len(placement_candidates) == 1
    ):
        catalog_id = next(iter(placement_candidates))
        return EmbyMappingAuditItem(
            emby_item_id=item_id, item_name=name, media_type=media_type,
            catalog_item_id=catalog_id, classification="placement_title",
            evidence_source="active_placement_title", candidate_count=1,
            distinct_candidate_ids=[catalog_id],
            duplicate_emby_name_count=duplicate_name_count,
            placement_title_collision_count=0, apply_eligible=True,
            detail="Unique exact active placement title resolves one channel.",
        )
    if (
        duplicate_name_count > 1 or len(title_candidates) > 1
        or (media_type == "channel" and not title_candidates
            and len(placement_candidates) > 1)
    ):
        candidates = title_candidates or placement_candidates
        return EmbyMappingAuditItem(
            emby_item_id=item_id, item_name=name, media_type=media_type,
            classification="ambiguous", candidate_count=len(candidates),
            distinct_candidate_ids=_bounded_ids(candidates),
            duplicate_emby_name_count=duplicate_name_count,
            catalog_title_collision_count=catalog_title_collision_count,
            placement_title_collision_count=placement_collision_count,
            ineligibility_reason="duplicate_title_evidence",
            detail="Exact normalized title evidence is not unique.",
        )
    return EmbyMappingAuditItem(
        emby_item_id=item_id, item_name=name, media_type=media_type,
        series_name=series_name, season_number=season_number,
        episode_number=episode_number, classification="unmatched",
        duplicate_emby_name_count=duplicate_name_count,
        catalog_title_collision_count=catalog_title_collision_count,
        placement_title_collision_count=placement_collision_count,
        ineligibility_reason="no_safe_exact_match",
        detail="No safe exact identity or title evidence resolved a catalog item.",
    )


def preview_emby_mapping_audit(
    payload: EmbyMappingAuditRequest,
) -> EmbyMappingAuditResponse:
    settings = emby._private_settings()
    if not settings.get("server_url") or not settings.get("api_key"):
        raise emby.EmbyError(
            "Emby server URL and API key are required.", "not_configured",
        )
    info = emby._request_json("/System/Info", settings)
    if not isinstance(info, dict):
        raise emby.EmbyError("Emby returned an unexpected server response.", "error")
    server_id = emby._safe_id(info.get("Id")) or "unknown"
    requested = set(payload.media_types)
    scanned, scan_complete = _scan_library(settings, requested)

    emby_name_counts: Counter[tuple[str, str]] = Counter()
    emby_episode_counts: Counter[tuple[str, int, int]] = Counter()
    normalized_items: list[tuple[dict[str, Any], str]] = []
    for item in scanned:
        media_type = _audit_media_type(item)
        if media_type not in requested:
            continue
        normalized_items.append((item, media_type))
        emby_name_counts[(media_type, _normalize(item.get("Name")))] += 1
        if media_type == "episode":
            series_name = _normalize(item.get("SeriesName"))
            season = _safe_number(item.get("ParentIndexNumber"))
            episode_number = _safe_number(item.get("IndexNumber"))
            if series_name and season is not None and episode_number is not None:
                emby_episode_counts[(series_name, season, episode_number)] += 1

    with closing(_read_only_connect()) as conn:
        context = _catalog_context(conn, requested, server_id)
        collisions = EmbyMappingAuditCollisionTotals()
        details = [
            _classify_item(
                item, media_type, server_id, context,
                emby_name_counts, emby_episode_counts, collisions,
            )
            for item, media_type in normalized_items
        ]

    details.sort(key=lambda item: (
        AUDIT_MEDIA_TYPES.index(item.media_type),
        _normalize(item.item_name),
        item.emby_item_id or "",
    ))
    classifications = Counter(item.classification for item in details)
    evidence = Counter(
        item.evidence_source for item in details if item.evidence_source
    )
    totals = Counter(item.media_type for item in details)
    page = details[payload.offset:payload.offset + payload.limit]
    return EmbyMappingAuditResponse(
        integration_id=server_id,
        generated_at=datetime.utcnow(),
        requested_media_types=payload.media_types,
        totals_by_media_type={
            media_type: totals.get(media_type, 0) for media_type in payload.media_types
        },
        classification_counts={
            classification: classifications.get(classification, 0)
            for classification in AUDIT_CLASSIFICATIONS
        },
        evidence_source_counts={
            source: evidence.get(source, 0) for source in AUDIT_EVIDENCE_SOURCES
        },
        collision_totals=collisions,
        scanned_count=len(details),
        scan_complete=scan_complete,
        truncated=not scan_complete,
        scan_cap=EMBY_AUDIT_SCAN_CAP,
        total_details=len(details),
        offset=payload.offset,
        limit=payload.limit,
        returned_count=len(page),
        items=page,
    )
