from __future__ import annotations

import sqlite3

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from app.services.logs import add_log
from app.services.outputs import build_live_m3u_document


router = APIRouter(tags=["distribution"])


@router.get(
    "/live/playlist.m3u",
    response_class=Response,
    responses={
        200: {"content": {"application/x-mpegURL": {}}},
        503: {"description": "The authoritative live playlist could not be read."},
    },
)
def live_playlist() -> Response:
    """Serve current catalog state; requesting the playlist never reserves a stream."""
    try:
        document = build_live_m3u_document()
    except (sqlite3.Error, OSError, ValueError) as exc:
        add_log("error", "outputs", f"Native Live M3U unavailable error={type(exc).__name__}")
        raise HTTPException(
            status_code=503,
            detail="The live playlist is temporarily unavailable. Check Media Router output and runtime URL settings.",
        ) from exc
    return Response(
        content=document.content.encode("utf-8"),
        headers={
            "Cache-Control": "no-cache",
            "Content-Type": "application/x-mpegURL; charset=utf-8",
            "ETag": f'"{document.digest}"',
        },
    )
