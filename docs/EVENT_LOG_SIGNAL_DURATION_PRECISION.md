# Saved event-log signal duration precision

The local, read-only event-log signal evaluator now computes each case's
**elapsed seconds** with DuckDB `epoch(max(ts) - min(ts))`, retaining
fractional seconds from the imported timestamps. Previously
`date_diff('second', min(ts), max(ts))` counted integer clock-second
boundaries crossed, **not** actual elapsed seconds. Depending on the
fractional start/end alignment, this could overstate or understate elapsed
time and produce false-positive or false-negative fractional KPI alerts.

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
evaluation state. A second synthetic boundary-crossing regression checks
00:00:00.900 → 00:00:01.100 (0.2 s, not 1 s) and
00:01:00.900 → 00:01:02.100 (1.2 s, not 2 s), including median and p90
thresholds that previously could produce false positives. It does **not**
establish real-provider or consenting
customer acceptance. Source event timestamps may be incomplete,
rounded, delayed or inaccurate; preserving subsecond arithmetic does not
verify source timing or business outcomes.

Signals are descriptive observations about the saved log, never verified
causes, individual productivity scores, approval to act, or instructions
to execute an automation. Existing local-only storage, consent,
authorization, tenant isolation, retention, and no-action boundaries
remain in force.
