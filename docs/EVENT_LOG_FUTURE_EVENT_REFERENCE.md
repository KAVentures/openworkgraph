# Event-log future-date reference semantics (#326 R6)

The saved event-log quality report counts events whose stored UTC timestamps
are later than a reference instant. The count is a data-quality observation,
not evidence that a source exported a complete history or that the event is
fraudulent. Source clocks, scheduled future work and mapping mistakes can
all explain a future-dated event.

Each newly generated `diagnostics` object now includes:

- `events_dated_after_reference`: the number of supplied events later than
  the disclosed UTC reference instant.
- `future_event_reference.at`: the exact ISO-8601 UTC comparison instant.
- `future_event_reference.basis`: `report_created` for an immutable
  import/append report, or `current_query` for freshly recomputed diagnostics.
- `events_dated_after_import`: retained **unchanged** as a compatibility
  alias of the count. For `current` diagnostics its historical name is
  misleading: it has always compared to the time of the query, not the
  original import. Consumers needing unambiguous semantics should use the
  new count and reference metadata.

For a new import or append, `future_event_reference.at` is identical to
the saved report's `created_at`, and that same instant is used for the
comparison and report timestamp in local storage. Re-reading an immutable
report does not recalculate the count. The `current` view does recalculate
against the time it was requested, so a future-dated event may stop being
counted as time passes.

Historical reports created before this change retain their original
diagnostic fields and are not rewritten or assigned invented reference times.
This change does not modify stored events, source identities, v1/v2 digests,
retention, tenant isolation, authorization, API access or source reads.

Synthetic UTC/offset and time-boundary tests cover the comparison and the
legacy alias. No customer/provider acceptance or source-completeness claim
is made.

## Dashboard reference disclosure

The Data quality panel displays the live `current` diagnostic, labels its
comparison as **current query**, and shows the exact UTC reference instant
returned by the API alongside the future-event count. It does not label a
recomputed count as "after import". Historical import/append reports keep
their own immutable `report_created` reference in the API.

For an older server that returns only `events_dated_after_import`, the panel
uses that legacy count but explicitly says the exact reference was **not
recorded**. It does not fabricate a comparison timestamp. The real-browser
acceptance test compares the rendered count and reference to the API response.
