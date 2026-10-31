# Event-log KPI and root-cause duration precision

Local event-log KPI throughput and the `duration_over` descriptive root-cause
outcome measure **elapsed time** with DuckDB
`epoch(max(ts) - min(ts))` in seconds, retaining fractional seconds instead
of truncating to whole-second boundaries with
`date_diff('second', min(ts), max(ts))`.

The existing positive integer SLA and root-cause thresholds retain their
strict **duration > threshold_seconds** semantics. An exactly 1.000-second
case does not breach a 1-second threshold, while a 1.500-second case does.
This applies consistently to KPI p50/p90/p95/mean, weekly median throughput,
KPI SLA counts/rates, and root-cause `duration_over` outcome counts. It
aligns with the existing H4 drift duration representation and the separately
claimed cohort benchmark precision correction (#501).

A synthetic imported CSV regression checks 0.5, 1.0, 1.5, and 2.75-second
cases, percentiles, the weekly median, and both strict threshold boundaries.
This is not real-provider or consenting-customer acceptance. Source timestamps
may themselves be incomplete, delayed, inaccurate or coarsely rounded; greater
numeric precision does **not** establish more accurate business timing.
Root-cause associations remain descriptive and non-causal, with no claim about
individual performance. No new source access, capture, permissions, retention
rules, endpoint, response keys, upload or execution authority is introduced.
