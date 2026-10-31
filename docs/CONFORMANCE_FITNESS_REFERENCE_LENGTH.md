# Conformance fitness: normalize against the selected reference path

The local event-log conformance analyzer compares each observed case variant
against up to ten **user-supplied** reference paths. It selects the path with
the fewest unit-cost log/model alignment moves (first listed path breaks an
equal-cost tie). The reported fitness for that variant is now

```
1 - alignment_cost / (observed_trace_length + selected_reference_path_length)
```

Previously, the denominator used the shortest path anywhere in the supplied
model, even if the best alignment selected a longer path. That could
**understate** fitness and make adding an unrelated short alternative change
the score of an otherwise identical observed variant. The fix retains the
minimum-cost alignment and tie-breaking behavior, case-weighted aggregation,
omission counts, bounded worst-variant evidence and existing response keys.

Synthetic example: an observed `A → B → C` trace compared with reference
paths `A` and `A → B → C → D` aligns best to the second path (one missing
`D`). Its fitness is `1 - 1/(3+4) = 0.857143`, not `1 - 1/(3+1) = 0.75`.
Adding or removing the unrelated short path does not alter that score.
A deterministic mocked-query regression checks both reference-path orders,
the single-path control, selected path index, and deviation evidence.
A separate synthetic regression checks that diagnostic `cases` counts for
`skipped_model_activities` and `extra_observed_activities` mean **distinct
cases containing each missing or extra activity**, not the number of repeated
alignment moves. For example, one case with two missing `A` moves counts as
one affected case for `A`, not two; its `share_of_aligned_cases` cannot
exceed 1.0. Individual repeated moves remain visible in the bounded
`deviating_variants[].deviations` evidence. This corrects formerly inflated
diagnostic counts and shares without changing alignment costs, fitness,
reference-path selection or the response schema. These are **synthetic
contract tests**, not native multi-platform test results.

This correction can change historical displayed conformance scores for
multi-path models; previously stored raw events and reference paths are
unchanged. Fitness is descriptive similarity to a *user-declared* model,
not verified policy adherence, process correctness, business outcomes,
causal attribution or permission for an agent to act. The change adds no
capture, provider call, tenant access, upload, persistence, new permission,
REST/MCP endpoint or dashboard behavior. Native CI, including cross-platform
pytest, browser, process collaboration, scale, accessibility, starter and
packaging, must pass on the exact PR head before merging.
