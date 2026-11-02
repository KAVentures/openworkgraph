# Cohort overlap and interval interpretation (R6)

Saved event-log cohort benchmarks compute descriptive differences between two
user-selected sets of cases. Their existing risk-difference confidence intervals
use independent-group Newcombe intervals; the throughput median difference
uses independently resampled bootstrap draws. Neither is a paired/overlap-aware
interval.

When the two cohorts share cases, the direction of interval miscalibration is
**not known in general**. Depending on covariance and outcome structure, an
independent-group interval can be too narrow or too wide for the comparison
actually being made. The old warning that such intervals *always overstate
certainty* was therefore incorrect. The response now says:

> cohorts share cases; independent-cohort intervals may understate or overstate uncertainty

The existing `overlapping_cases` count and
`intervals_assume_independent_cohorts: true` flag remain available for
reviewers and downstream consumers. For completely identical cohorts the
observed second-minus-first difference is exactly zero, although an
independent-group confidence interval can have nonzero width. A synthetic
regression covers this case and a partially overlapping comparison.

The **overlap-warning correction itself is wording only**: no statistical formula, sampled case
selection, point estimate, CI, JSON key, endpoint, stored event, permission,
tenant isolation, consent, retention, or export behavior is changed. An
overlap-aware paired analysis would require separately specified estimands,
sampling assumptions, and validation. Do not interpret these descriptive
associations as causal findings, business outcomes, or permission to execute
an agent action. Synthetic regressions are not real-provider or consenting-
customer acceptance.

## Direct-call benchmark display limit validation

The optional Python-only `benchmark(..., limit=...)` parameter caps the
number of reported variants (at most 50) and activity rows (at most twice
that cap). It is **not** an input exposed by the authenticated REST benchmark
endpoint. Previously the direct function coerced `int(limit)` before
clamping, silently truncating fractions, accepting booleans or numeric
strings, and leaking raw `TypeError`, `ValueError` or `OverflowError` for
malformed or nonfinite inputs.

Direct calls now require a genuine Python integer, excluding `bool`, and
raise `EventLogWorkspaceError` for other types **before querying saved
logs**. Existing integer clamping to 1–50 is unchanged, including values
outside that range; the REST/MCP/dashboard response contract, sampled
intervals, point estimates, case selection, authorization and stored data
are unchanged. Synthetic mocked-query tests cover malformed values and
the actual query closure's integer clamp, not native DuckDB, HTTP,
cross-platform, real-provider or consenting-customer acceptance.
