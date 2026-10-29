# Event-log timestamp ties: distinct-event and case counts (#326 R6)

The saved event-log quality report exposes two **counts-only** diagnostics:

- `events_sharing_a_timestamp_in_a_case` is the number of **distinct stored events**
  that share the same timestamp with at least one *other* distinct event linked
  to the same retained object. One event linked to several objects with tied
  timestamps counts once, not once per object. An event linked to one object
  under several relationship qualifiers is still only one event.
- `cases_with_shared_timestamps` is the number of **distinct retained objects**
  with at least one such timestamp tie. In OCEL, objects of any type can be case
  notions; this does not mean only objects literally typed `case`.

Both counts apply to the supplied/retained event-object relationships in the
requested report scope. They do **not** infer a source-system case identity,
a causal relationship, a real observed event order, or complete coverage.
Events with equal timestamps still use event-ID fallback ordering in downstream
views; that deterministic tie-breaker is not evidence of execution order.

The correction prevents multi-object memberships from inflating the legacy
event count. It preserves the existing REST field names, report structure,
stored events, content digests, consent, permissions, tenant isolation and
retention behavior. Previously persisted quality reports remain historical
snapshots; newly computed current diagnostics and future stored reports use the
corrected count.

The regression fixture covers two events tied in both an order and an invoice,
repeated qualifiers on one event-object link, an independent second tie, and
counts-only disclosure. These are synthetic DuckDB tests, not live-provider
validation or proof of real-customer process completeness.
