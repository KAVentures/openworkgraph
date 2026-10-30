# Concept drift: windows without observed transitions

Scope: roadmap #326 H4, Worker A R6 analytics quality. This is an additive
response/interpretation correction; it does not change imported events,
case selection, consent, authorization, tenant isolation, retention, or
agent execution rights.

## Why the guard matters

The concept-drift statistic compares **directly-follows distributions**
between adjacent sampled windows. A case containing only one event has no
directly-follows edge. If every sampled case in a window has one event,
that window has no edge distribution at all. A case-count threshold alone
cannot establish that a Jensen–Shannon comparison is meaningful.

Previously, a window could pass `min_cases` but have zero observed paths.
A one-sided empty distribution could be treated as a large distance, or
two empty windows could be reported as a tested non-change. Neither is
evidence that the recorded process changed or remained stable.

## Additive contract

Each `windows[]` entry now includes `sampled_cases_with_paths`: the
number of sampled cases with at least one observed directly-follows
transition. It is at most `sampled_cases`. Counts are aggregate only;
no case IDs or additional raw event text are returned.

A boundary is tested only when **both** windows meet `min_cases` on
actual sampled cases **and** at least `min_cases` of those sampled cases
have a directly-follows path. A 40-case window with just one path-bearing
case cannot support a 30-case minimum for a distribution-based test.

If a boundary has enough sampled cases but either side has zero paths,
it is included in `boundaries_not_tested[]` with:

- `reason: "no_directly_follows_paths"`
- `missing_paths_side: "before" | "after" | "both"`
- `sampled_cases_with_paths_before/after` (aggregate counts)
- `insufficient_side: null` (case-count support is sufficient)

If both sampled-case counts meet the floor and neither path-bearing count
is zero, but one or both path-bearing counts fall below `min_cases`,
the boundary is also omitted, with `reason: "insufficient_path_support"`,
`insufficient_path_support_side: "before" | "after" | "both"`, the same
aggregate path-bearing counts and `insufficient_side: null`.
`boundaries_skipped_sparse_paths` counts these boundaries. This is a
conservative support requirement, not a proof of statistical power or
representativeness.

These are **not** change points or tested non-change boundaries.
`boundaries_skipped_no_paths` counts the zero-path category. The existing
`boundaries_skipped_small_windows` remains restricted to boundaries
with insufficient sampled-case support, preserving its original meaning
and the original small-window omission record shape. A boundary with
both insufficient case support and no paths remains categorized as
insufficient sampled cases.

The invariant is:
`boundaries_tested + boundaries_skipped_small_windows +
boundaries_skipped_no_paths + boundaries_skipped_sparse_paths
== max(0, len(windows) - 1)`.

The JSD, permutation count, Bonferroni correction and tested-boundary
statistics are unchanged. The support guard is stricter: it withholds
inferential labels for windows where too few sampled cases contribute
any path observations. It does **not** establish that the input source captured
every step; partial observation and changes in instrumentation remain
alternative explanations for apparent drift.

## Validation

Synthetic CSV tests cover a pathless window before a normal window,
a pathless window after a normal window, two pathless windows,
the 29/30 path-bearing-case threshold with one-sided and two-sided
sparse support, and authenticated local HTTP disclosure. Existing tests cover actual sampled
case thresholds and stable versus changed paths. These tests are not
real-provider or consenting customer acceptance, and do not establish
causality or authorize any action.
