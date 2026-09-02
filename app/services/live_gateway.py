from __future__ import annotations

import asyncio
from dataclasses import dataclass
from threading import Lock
import time
from typing import Callable, Iterator, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from app.services.broker import confirm_reservation, heartbeat_reservation, release_reservation
from app.services.logs import add_log


LIVE_GATEWAY_CHUNK_SIZE = 64 * 1024
LIVE_GATEWAY_CONNECT_TIMEOUT_SECONDS = 10
LIVE_GATEWAY_HEARTBEAT_SECONDS = 15
LIVE_GATEWAY_MAX_ATTEMPTS = 3
LIVE_GATEWAY_MAX_REDIRECTS = 3

_OWNER_LOCK = Lock()
_RESERVATION_OWNERS: dict[str, int] = {}

FORWARDED_REQUEST_HEADERS = {
    "accept": "Accept",
    "accept-encoding": "Accept-Encoding",
    "icy-metadata": "Icy-MetaData",
    "range": "Range",
    "user-agent": "User-Agent",
}
FORWARDED_RESPONSE_HEADERS = {
    "accept-ranges": "Accept-Ranges",
    "cache-control": "Cache-Control",
    "content-encoding": "Content-Encoding",
    "content-length": "Content-Length",
    "content-range": "Content-Range",
    "content-type": "Content-Type",
    "icy-br": "Icy-Br",
    "icy-description": "Icy-Description",
    "icy-genre": "Icy-Genre",
    "icy-metaint": "Icy-MetaInt",
    "icy-name": "Icy-Name",
}


class LiveGatewayError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def _validated_upstream_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise LiveGatewayError("invalid_upstream_url", "The selected live source URL is invalid.") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise LiveGatewayError("invalid_upstream_url", "The selected live source must use HTTP or HTTPS.")
    return value


def _request_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {target: headers[source] for source, target in FORWARDED_REQUEST_HEADERS.items() if headers.get(source)}


def _open_upstream(url: str, headers: Mapping[str, str]):
    opener = build_opener(_NoRedirect())
    current = _validated_upstream_url(url)
    for redirect_count in range(LIVE_GATEWAY_MAX_REDIRECTS + 1):
        request = Request(current, headers=_request_headers(headers), method="GET")
        try:
            response = opener.open(request, timeout=LIVE_GATEWAY_CONNECT_TIMEOUT_SECONDS)
            if int(response.status) not in {200, 206}:
                response.close()
                raise LiveGatewayError("upstream_terminal_status", "The live provider returned an unusable status.")
            return response
        except HTTPError as exc:
            if exc.code in {301, 302, 303, 307, 308}:
                location = exc.headers.get("Location")
                exc.close()
                if not location or redirect_count >= LIVE_GATEWAY_MAX_REDIRECTS:
                    raise LiveGatewayError("upstream_redirect_limit", "The live provider redirect limit was exceeded.")
                current = _validated_upstream_url(urljoin(current, location))
                continue
            exc.close()
            raise LiveGatewayError("upstream_terminal_status", "The live provider returned an unusable status.") from exc
        except (TimeoutError, URLError, OSError) as exc:
            raise LiveGatewayError("upstream_connection_failed", "The live provider connection failed.") from exc
    raise LiveGatewayError("upstream_redirect_limit", "The live provider redirect limit was exceeded.")


@dataclass
class LiveGatewaySession:
    reservation_id: str
    source_availability_id: int
    upstream: object
    status_code: int
    headers: dict[str, str]
    heartbeat_interval_seconds: float = LIVE_GATEWAY_HEARTBEAT_SECONDS

    def __post_init__(self) -> None:
        self._release_lock = Lock()
        self._released = False
        with _OWNER_LOCK:
            _RESERVATION_OWNERS[self.reservation_id] = _RESERVATION_OWNERS.get(self.reservation_id, 0) + 1

    def release(self, reason: str) -> None:
        with self._release_lock:
            if self._released:
                return
            self._released = True
        try:
            self.upstream.close()
        finally:
            with _OWNER_LOCK:
                remaining = _RESERVATION_OWNERS.get(self.reservation_id, 1) - 1
                if remaining > 0:
                    _RESERVATION_OWNERS[self.reservation_id] = remaining
                else:
                    _RESERVATION_OWNERS.pop(self.reservation_id, None)
            if remaining <= 0:
                release_reservation(self.reservation_id, reason=reason)
                add_log("info", "gateway", f"live_gateway_released reservation={self.reservation_id} reason={reason}")

    def iter_bytes(self) -> Iterator[bytes]:
        last_heartbeat = time.monotonic()
        reason = "live_gateway_eof"
        try:
            while True:
                chunk = self.upstream.read(LIVE_GATEWAY_CHUNK_SIZE)
                if not chunk:
                    break
                now = time.monotonic()
                if now - last_heartbeat >= self.heartbeat_interval_seconds:
                    heartbeat_reservation(self.reservation_id, source="live_gateway_activity")
                    last_heartbeat = now
                yield chunk
        except GeneratorExit:
            reason = "live_gateway_disconnect"
            raise
        except BaseException:
            reason = "live_gateway_stream_failure"
            raise
        finally:
            self.release(reason)

    async def iter_bytes_async(self):
        """Stream without blocking the event loop and release on task cancellation."""
        last_heartbeat = time.monotonic()
        reason = "live_gateway_eof"
        try:
            while True:
                chunk = await asyncio.to_thread(self.upstream.read, LIVE_GATEWAY_CHUNK_SIZE)
                if not chunk:
                    break
                now = time.monotonic()
                if now - last_heartbeat >= self.heartbeat_interval_seconds:
                    await asyncio.to_thread(
                        heartbeat_reservation,
                        self.reservation_id,
                        source="live_gateway_activity",
                    )
                    last_heartbeat = now
                yield chunk
        except asyncio.CancelledError:
            reason = "live_gateway_disconnect"
            raise
        except GeneratorExit:
            reason = "live_gateway_disconnect"
            raise
        except BaseException:
            reason = "live_gateway_stream_failure"
            raise
        finally:
            self.release(reason)


AcquireLiveSource = Callable[[set[int]], tuple[object, str]]


def open_live_gateway(
    acquire: AcquireLiveSource,
    request_headers: Mapping[str, str],
    *,
    max_attempts: int = LIVE_GATEWAY_MAX_ATTEMPTS,
) -> LiveGatewaySession:
    """Commit each reservation in acquire() before any upstream network access."""
    excluded: set[int] = set()
    last_error: LiveGatewayError | None = None
    for attempt in range(max(1, max_attempts)):
        try:
            reservation, raw_location_ref = acquire(excluded)
        except Exception:
            if last_error is not None:
                raise last_error
            raise
        reservation_id = reservation.reservation_id
        source_id = int(reservation.source_availability_id)
        try:
            upstream = _open_upstream(raw_location_ref, request_headers)
        except LiveGatewayError as exc:
            last_error = exc
            excluded.add(source_id)
            release_reservation(reservation_id, reason=exc.code)
            add_log("warning", "gateway", f"live_gateway_upstream_failed reservation={reservation_id} source={source_id} attempt={attempt + 1} reason={exc.code}")
            continue
        confirm_reservation(reservation_id, source="live_gateway_upstream_connected")
        response_headers = {
            target: upstream.headers[source]
            for source, target in FORWARDED_RESPONSE_HEADERS.items()
            if upstream.headers.get(source)
        }
        add_log("info", "gateway", f"live_gateway_opened reservation={reservation_id} source={source_id} status={upstream.status} attempt={attempt + 1}")
        return LiveGatewaySession(
            reservation_id=reservation_id,
            source_availability_id=source_id,
            upstream=upstream,
            status_code=int(upstream.status),
            headers=response_headers,
        )
    raise last_error or LiveGatewayError("upstream_connection_failed", "The live provider connection failed.")
