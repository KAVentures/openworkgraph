# Bounded variant previews

The main analysis overview uses the owner-only, no-store
`GET /v1/event-logs/{log_id}/variants/preview` endpoint. It groups complete
literal activity arrays and calculates case counts, distinct variants, shares,
and median durations before shortening any displayed path. A preview contains
at most 100 activity labels per variant, with `path_activity_count` and
`path_truncated` describing any omission. Two different long paths with the
same displayed prefix remain distinct variants.

The dashboard marks shortened paths, disables exact-path case inspection for
them, and does not use a shortened prefix as a reference model. Browse cases by
activity instead. Case filters apply to the preview in the same way as to the
other filtered analysis views. The original full `/variants` endpoint remains
available to existing clients.

The bound applies to returned path labels and dashboard rendering. DuckDB still
materializes and groups complete paths to preserve exact counts; this is not a
constant-memory guarantee for the full query.
