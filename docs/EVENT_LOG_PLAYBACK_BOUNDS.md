# Process-map and playback bounds

The process map and case playback rank all activities by frequency but fetch at
most 64 activity summaries into the application. The selection percentage uses
the event count across **all** activities, including hidden ones; hidden counts
remain exact. The two views use the same frequency and label ordering.

Playback selects at most 500 cases and returns at most 30,000 event points under
the standard limits. For each sampled case, it orders by timestamp and event ID
and applies the per-case point limit inside the query before constructing the
list returned to the application. `events_truncated` and `cases_truncated` still
disclose cases whose later events are omitted. Equal timestamps remain in event
ID order and jump instantly. The origin and span describe all retained events
in the selected activities, not only returned points.

These bounds apply to activity summaries and returned event lists. DuckDB must
still scan, group, and order the selected data, and the workspace's existing
case-event projection is materialized. They are not constant-memory guarantees
for the full query or evidence of multi-user or enterprise-scale acceptance.
