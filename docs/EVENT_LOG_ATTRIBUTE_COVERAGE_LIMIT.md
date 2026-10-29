# Bounded event-log attribute coverage (#326 R6)

The local event-log quality diagnostics intentionally return at most **32 distinct
attribute keys** in `attribute_coverage`, sorted by descending number of events
with a value, then by key. This is a bounded, counts-only diagnostic, not an
exhaustive schema inventory or evidence of source completeness.

The additive fields now make the cap explicit:

- `attribute_coverage_total_keys`: number of distinct keys encountered among
  the supplied events in this report's log or batch scope, **before** the cap.
- `attribute_coverage_truncated`: `true` if the displayed list omits any
  distinct keys; `false` for 0–32 keys.
- `attribute_coverage`: the existing list of up to 32 keys with
  `events_with_value` and `events_without_value`. These counts and their
  sorting are unchanged. JSON `null` and empty strings are missing; `false`
  and numeric zero are present.

A key absent from the displayed list **must not** be interpreted as absent
from the log when `attribute_coverage_truncated` is true. The total key count
does not disclose omitted key names or values. It does not guarantee that
source systems exported all relevant fields or records.

The additional count comes from the same grouped DuckDB query as the existing
top-32 list, rather than a second pass over all events. Historical persisted
quality reports retain their original fields; newly generated reports and
current diagnostics include these additive indicators.

No changes to imports, stored events, source identities, content hashes,
retention, authorization, tenant boundaries, source access, or action authority.
Regression fixtures are synthetic; real-provider and customer acceptance
remain separate.
