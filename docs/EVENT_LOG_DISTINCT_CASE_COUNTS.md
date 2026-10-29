# Distinct-event case and timestamp quality counts (#326 R6)

The local event-log quality report counts **events**, not the number of
event-object relationship rows, when measuring case sizes and timestamp ties.

An event can legitimately reference the same object more than once with
different OCEL relationship qualifiers. These are distinct qualified links,
but they are still **one event** for the following counts:

- `case_notions[].cases`: number of distinct objects for that object type
  with at least one linked event in the supplied log scope.
- `case_notions[].single_event_cases`: number of such objects linked to
  exactly one distinct event ID, even when that event has multiple qualifiers.
- `events_sharing_a_timestamp_in_a_case`: sum of distinct event counts in
  each object-and-timestamp group containing at least two distinct events.
  One event with two qualified links cannot create a tie by itself.
- `cases_with_shared_timestamps`: distinct objects with at least one such
  genuine multi-event timestamp tie.

The timestamp count is an **event-object incidence count**: an event linked
to multiple distinct objects may contribute to each object's tie. It is not
a global count of unique event IDs. Equal timestamps do not establish
observation order; event IDs remain the deterministic fallback.

The qualifier-aware `duplicate_events` signature from #446 is unchanged:
it still compares all stored qualified links and attributes. Neither
diagnostic deduplicates or deletes stored events or relationships.

This is a counts-only correction. Previously persisted quality reports remain
unchanged; new import/append reports and current diagnostics use the corrected
count. No changes to public response fields, source IDs, content digests,
authorization, consent, retention, tenant boundaries, or action authority.
The regression uses an owned synthetic DuckDB fixture, not real-provider
or customer acceptance. Completeness of the underlying source remains unknown.
