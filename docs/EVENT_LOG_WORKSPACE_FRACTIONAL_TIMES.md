# Event-log workspace fractional elapsed-time analytics

The local, read-only event-log **summary**, **variants** and **directly-follows** views calculate elapsed seconds using DuckDB interval `epoch(...)`, preserving fractional seconds from imported timestamps:

- Summary `case_duration_seconds` p50/p90/mean: `epoch(max(ts) - min(ts))` per case.
- Variant `median_duration_seconds`: the median of those elapsed case durations within each observed activity path.
- Directly-follows `median_gap_seconds`: `epoch(lead(ts) OVER (PARTITION BY case_id ORDER BY ts, event_id) - ts)` per observed adjacent transition.

Previously, `date_diff('second', ...)` truncated these values to integer seconds. For example, a 0.125-second transition appeared as zero seconds, and a 1.5-second case as one second. The new calculation aligns with the separately claimed cohort and KPI precision work in #501 and #502. It preserves the existing response field names, route and MCP compatibility, case-notion selection, local-only storage, consent, authorization and retention rules.

The synthetic CSV regression includes microsecond timestamps, UTC-offset-equivalent instants, multiple paths, and a 0.125-second edge. It checks summary quantiles and mean, per-path medians, and directly-follows gap medians. This regression is **not** real-provider or consenting-customer acceptance.

Precision is bounded by the source timestamps. Imported events may be missing, delayed, rounded or reordered, and a directly-follows gap is **observed inter-event time**, not verified waiting, processing time, business causation or individual productivity. These views do not authorize agent execution or claim verified business outcomes.
