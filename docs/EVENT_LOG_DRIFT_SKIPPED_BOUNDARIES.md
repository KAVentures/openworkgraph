# Concept-drift untested-boundary provenance

The read-only `/v1/event-logs/{log_id}/drift` result now includes an additive
`boundaries_not_tested` array. It lists each adjacent window pair that did **not**
meet the requested `min_cases` threshold in the *actual statistical sample*.
It does not imply that a missing comparison is a stable process.

Each entry contains `before` and `after` window labels (matching
`windows[].window`), `sampled_cases_before`, `sampled_cases_after`, and
`insufficient_side` (`before`, `after`, or `both`). These are aggregate
counts, not case identifiers, raw events or evidence links. The sample may be
smaller than the eligible window because the existing deterministic
`sample_per_window` cap is applied before the minimum-support check.

The existing `boundaries` array still contains only **tested** comparisons;
`change_points` remains its flagged subset. The existing
`boundaries_skipped_small_windows` count equals the length of
`boundaries_not_tested`, and
`boundaries_tested + boundaries_skipped_small_windows` equals the number of
adjacent pairs, `max(0, len(windows) - 1)`. The statistical test, significance
threshold, sampling, window order and all existing fields are unchanged.

A skipped comparison has **unknown** change status, not a non-significant
result. Even a tested change point describes recorded path differences, not a
verified cause, business workflow, authorization or permission to act.
Synthetic 25/10/25-case and sample-cap regressions verify one-sided and
both-sided suppression and the accounting invariant. These tests do not
constitute a real-provider integration or consenting customer pilot.
