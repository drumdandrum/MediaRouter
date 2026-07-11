# Outputs Module

Owns generated outputs.

Sprint 6 responsibilities:

- STRM output

Sprint 7 responsibilities:

- Live TV M3U output

Planned plugins:

- XMLTV output
- HDHomeRun output
- REST API output

Outputs consume catalog and runtime URL contracts rather than provider URLs directly. Generated files are disposable artifacts and should be safe to delete and rebuild.
