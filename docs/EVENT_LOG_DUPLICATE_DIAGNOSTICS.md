# Event-log duplicate diagnostics: exact stored signatures (#326 R6)

The local quality report's counts-only `diagnostics.duplicate_events` measures
**excess event rows** that share a stored signature, excluding the event ID:
activity, UTC timestamp, resource, stored attributes, and the sorted set of
event-object links **including each link's qualifier**. For a group of three
matching events it counts two excess rows. No event rows are removed.

A shared timestamp, activity, resource, and case object alone does **not**
establish a duplicate: different attributes or different link qualifiers
represent different stored evidence. This diagnostic is separate from
`events_sharing_a_timestamp_in_a_case`, which reports timestamp ties even
when the underlying events differ.

The signature compares the stored JSON attribute representation, not a
semantic normalization of differently ordered JSON keys. The count describes
only the supplied event log and is not evidence that a provider emitted
duplicate business actions, that a case is complete, or that any event should
be deleted. No source values, event IDs, object IDs, or qualifier values are
returned by the count.

This correction preserves the existing field name and response schema, stored
rows, source identities, content SHA-256 v1/v2, imports, consent, permissions,
retention and action boundaries. Existing historical quality reports remain
immutable; newly written reports and current diagnostics use the corrected
signature. The regression uses synthetic local DuckDB rows, not customer or
provider acceptance.
