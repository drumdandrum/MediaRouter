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

STRM generation is global when `catalog_item_ids` is omitted or `null`. An
explicit bounded list targets only those movie/episode catalog IDs, leaves
unselected files and tracking rows untouched, and skips orphan cleanup. This is
intended for controlled integration work and targeted regeneration; it is not
a provider, account, feed, title, or output-path selector.
