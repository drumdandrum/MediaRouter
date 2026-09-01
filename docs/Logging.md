# Logging and Diagnostic Redaction

Media Router treats logs, API error details, and persisted job history as
operational diagnostics. They may retain identifiers needed to investigate a
failure, but they must not retain credentials or sensitive endpoint details.

## Redaction boundary

All in-memory and Uvicorn log messages pass through the shared diagnostic
redactor. Job messages and structured job results are sanitized before they are
written to `jobs.json`, including records restored from existing history. API
handlers sanitize exception text before returning it as an HTTP error detail.

The redactor removes or replaces:

- URL user information, non-root paths, and fragments;
- sensitive URL query values such as tokens, tickets, and signatures;
- authorization, cookie, password, secret, token, and API-key values;
- embedded newlines that could forge additional log records.

Safe correlation context remains visible where possible, including catalog,
reservation, binding, Emby item, provider, and account identifiers. Exception
tracebacks are not sent directly to the runtime logger in request and output-job
failure paths because traceback locals can contain unsanitized values.

## Backup diagnostics

Backup payloads preserve authoritative secret-bearing state with restrictive
filesystem permissions. Backup manifests and validation errors describe files
and failure classes, not secret values. Tests verify that API keys and playback
ticket secrets do not appear in generated manifests.

## Development rule

New diagnostics must use `app.services.logs.add_log`, sanitize API exception
details with `app.core.redaction.redact_text`, and place structured diagnostic
data through `app.core.redaction.redact_value`. Do not add direct traceback
logging for paths that process provider URLs, headers, credentials, or tokens.
