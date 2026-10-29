# Event-log attribute coverage: missing-value semantics (#326 R6)

The local event-log quality report contains a bounded, **counts-only**
`diagnostics.attribute_coverage` array. For each reported attribute key:

- `events_with_value` counts events whose JSON attribute value is present
  and is neither JSON `null` nor an empty JSON string (`""`).
- `events_without_value` is the total number of events in the report's
  scope minus `events_with_value`. It includes missing keys, explicit JSON
  `null`, and empty strings. JSON `false` and numeric `0` **are values**.
- Attribute names come from explicitly supplied/mapped data. No raw attribute
  values, employee names, or event samples are returned by this diagnostic.

The same rules apply to a stored batch report and the current whole-log
diagnostic. The existing limit of 32 reported attribute keys and their ordering
are unchanged. The correction affects only the counts for explicit JSON
`null` values; it does not change the response schema, imported events,
stored source identities, source/transform hashes, content digest v1/v2,
retention, consent, authorization, or whether a source log is complete.

These counts describe the **supplied rows only**. They do not establish
provider-side completeness, business correctness, or that a missing value
represents employee behavior. The regression uses an owned local DuckDB
fixture, not a live provider or customer dataset.
