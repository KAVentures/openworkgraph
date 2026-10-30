# Dotted-chart within-case endpoint thinning (#326 H2 / #264)

The read-only dotted-chart response is bounded by `MAX_POINTS` (currently 30,000) across at most `max_cases` displayed rows. After deterministic case-row selection, each displayed case receives a budget of `max(1, floor(MAX_POINTS / cases_shown))` dots.

For a displayed case with **more events than its dot budget**:

- With **one** dot available, the first recorded event is shown. The last event cannot also be represented.
- With **two or more** dots available, the first and last recorded events are always shown, and intermediate events are selected deterministically by evenly spaced rank boundaries. A case with `n` events and budget `p` (where `2 <= p < n`) yields exactly `p` dots, including ranks `0` and `n-1`.
- If all events fit, every event is shown without thinning. Events are ordered by timestamp and then stored event ID.

This changes only within-case point selection. The existing case-row sampling (including both ordered cohort endpoints when at least two rows fit), activity slots, absolute/relative time conventions, `duration_seconds`, `span_seconds`, `coverage` counts, REST/MCP/dashboard response fields, source rows, permissions, consent and retention remain unchanged. In relative mode, the last displayed dot now reaches the recorded case duration whenever two or more dots fit; in absolute mode, the final recorded timestamp is represented for each such case.

**Interpretation:** Retaining endpoints does **not** mean all intermediate events are represented. `coverage.events_omitted_by_point_thinning` still reports exactly how many events are absent from the displayed cases; `coverage.events_omitted_by_case_sampling` separately accounts for omitted rows. This is deterministic display thinning, not a statistically representative sample, a verified business workflow or execution authority.

Synthetic regression exercises two uneven cases with one, two, three and full dot budgets, timestamp endpoints, repeatability, bounded counts and omission conservation. It is not real-provider, real-customer or consenting-pilot acceptance.
