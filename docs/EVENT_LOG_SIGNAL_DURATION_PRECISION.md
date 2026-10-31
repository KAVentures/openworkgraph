# Saved event-log signal duration precision

The local, read-only event-log signal evaluator now computes each case's
**elapsed seconds** with DuckDB `epoch(max(ts) - min(ts))`, retaining
fractional seconds from the imported timestamps. Previously
`date_diff('second', min(ts), max(ts))` truncated to integer seconds.

The change affects only the existing case-duration basis for saved rules:
`median_throughput_seconds`, `p90_throughput_seconds`,
`sla_breach_rate`, and `duration_over`. Existing positive-integer SLA
and case-condition seconds and strict **duration > threshold** semantics
remain unchanged: exactly 1.000 seconds does not breach a 1-second
threshold, but 1.500 seconds does. Existing evidence case ordering,
new-since-last-check comparisons, rule persistence, log scoping, and
review invalidation are unchanged. No endpoint or response key changes.

A synthetic CSV regression checks 0.5, 1.0, 1.5 and 2.75-second cases,
median/p90, strict SLA breach rate, bounded case evidence, and unchanged
evaluation state. It does **not** establish real-provider or consenting
customer acceptance. Source event timestamps may be incomplete,
rounded, delayed or inaccurate; preserving subsecond arithmetic does not
verify source timing or business outcomes.

Signals are descriptive observations about the saved log, never verified
causes, individual productivity scores, approval to act, or instructions
to execute an automation. Existing local-only storage, consent,
authorization, tenant isolation, retention, and no-action boundaries
remain in force.
