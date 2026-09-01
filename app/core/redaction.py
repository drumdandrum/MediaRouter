from __future__ import annotations

import logging
import re
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit


REDACTED = "[redacted]"
SENSITIVE_KEYS = (
    "password", "passwd", "secret", "token", "access_token", "refresh_token",
    "api_key", "apikey", "authorization", "proxy_authorization", "cookie",
    "set_cookie", "ticket", "signature", "sig",
)
_KEY_PATTERN = "|".join(re.escape(key).replace("_", "[_-]?") for key in SENSITIVE_KEYS)
_URL_RE = re.compile(r"(?i)\b(?:https?|rtsp)://[^\s<>\"']+")
_QUOTED_VALUE_RE = re.compile(
    rf"(?i)([\"']?(?:{_KEY_PATTERN})[\"']?\s*[:=]\s*)([\"'])(.*?)(\2)"
)
_PLAIN_VALUE_RE = re.compile(
    rf"(?i)(\b(?:{_KEY_PATTERN})\b\s*[:=]\s*)(?!\[redacted\])[^\s,;&}}\]]+"
)
_AUTH_SCHEME_RE = re.compile(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+")
_AUTH_HEADER_RE = re.compile(
    r"(?i)(\b(?:authorization|proxy-authorization)\b\s*[:=]\s*)(?:Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+"
)
_SENSITIVE_QUERY_KEYS = {
    key.replace("_", "").replace("-", "") for key in SENSITIVE_KEYS
}


def _redact_url(match: re.Match[str]) -> str:
    raw = match.group(0)
    trailing = ""
    while raw and raw[-1] in ".,);]":
        trailing = raw[-1] + trailing
        raw = raw[:-1]
    try:
        parsed = urlsplit(raw)
        host = parsed.hostname or "host"
        if parsed.port:
            host = f"{host}:{parsed.port}"
        query = []
        for key, value in parse_qsl(parsed.query, keep_blank_values=True):
            normalized = re.sub(r"[_-]", "", key.lower())
            query.append((key, REDACTED if normalized in _SENSITIVE_QUERY_KEYS else value))
        path = "/[redacted]" if parsed.path not in {"", "/"} else parsed.path
        return urlunsplit((parsed.scheme, host, path, urlencode(query), "")) + trailing
    except (TypeError, ValueError):
        return "[redacted-url]" + trailing


def redact_text(value: object) -> str:
    """Return one-line diagnostic text with common credential forms removed."""
    text = str(value)
    text = _URL_RE.sub(_redact_url, text)
    text = _AUTH_HEADER_RE.sub(lambda match: f"{match.group(1)}{REDACTED}", text)
    text = _AUTH_SCHEME_RE.sub(lambda match: f"{match.group(1)} {REDACTED}", text)
    text = _QUOTED_VALUE_RE.sub(lambda match: f"{match.group(1)}{match.group(2)}{REDACTED}{match.group(4)}", text)
    text = _PLAIN_VALUE_RE.sub(lambda match: f"{match.group(1)}{REDACTED}", text)
    return text.replace("\r", "\\r").replace("\n", "\\n")


def redact_value(value):
    """Recursively sanitize strings stored in operational diagnostics."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            normalized = re.sub(r"[_-]", "", str(key).lower())
            redacted[key] = REDACTED if normalized in _SENSITIVE_QUERY_KEYS else redact_value(item)
        return redacted
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_value(item) for item in value)
    return value


class UvicornAccessRedactionFilter(logging.Filter):
    """Sanitize Uvicorn's raw request-target argument before formatting."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and len(record.args) >= 3:
            arguments = list(record.args)
            arguments[2] = redact_text(unquote(str(arguments[2])))
            record.args = tuple(arguments)
        return True


def install_uvicorn_access_redaction() -> None:
    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(item, UvicornAccessRedactionFilter) for item in logger.filters):
        logger.addFilter(UvicornAccessRedactionFilter())
