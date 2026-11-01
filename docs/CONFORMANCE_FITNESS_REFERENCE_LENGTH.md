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

## Direct-call variant cap validation

The authenticated REST route already declares `max_variants` as an integer
between 1 and `MAX_ALIGNED_VARIANTS`. Direct Python integrations previously
called `int(max_variants)` before applying the historical cap. This silently
truncated fractional values, accepted booleans and numeric strings, and could
raise raw `TypeError`, `ValueError` or `OverflowError` for missing, malformed
or nonfinite inputs.

Direct `conformance()` calls now require a genuine Python integer (not a
boolean) and raise `EventLogWorkspaceError` for other types **before any
saved-log query**. The existing direct integer clamp (including values outside
the REST range) remains unchanged, as do REST parameter validation and
response fields. Synthetic mocked-query regressions cover malformed values,
both clamp endpoints, and the resulting SQL variant limit; they do not
replace native DuckDB, HTTP, cross-platform or real-customer acceptance.

This correction can change historical displayed conformance scores for
multi-path models; previously stored raw events and reference paths are
unchanged. Fitness is descriptive similarity to a *user-declared* model,
not verified policy adherence, process correctness, business outcomes,
causal attribution or permission for an agent to act. The change adds no
capture, provider call, tenant access, upload, persistence, new permission,
REST/MCP endpoint or dashboard behavior. Native CI, including cross-platform
pytest, browser, process collaboration, scale, accessibility, starter and
packaging, must pass on the exact PR head before merging.

## Related R6 KPI input validation

The same analytics module exposes `kpis(..., sla_seconds=...)` to direct Python callers. Its authenticated REST endpoint already parses `sla_seconds` as an integer from 1 through 1,000,000,000. Direct calls now enforce that same inclusive bound and require a genuine Python integer (not a boolean), rejecting fractional values, numeric strings, nonfinite values and malformed types with `EventLogWorkspaceError` **before querying a saved log**. Omitting the SLA (`None`) remains valid. Existing valid integer thresholds, KPI results, REST/MCP/dashboard response shapes, consent and tenant controls are unchanged.

Parameterized synthetic tests cover invalid inputs and both inclusive bounds without a database. They are not native CI, real-provider acceptance, or consenting-customer validation.


## Related R6 root-cause parameter validation

The authenticated root-cause REST route already accepts integer
`threshold_seconds` values from 1 through 1,000,000,000 and integer
`limit` values from 1 through 100. Previously, direct Python
`root_cause()` callers could pass a fractional threshold or display limit
that `int()` silently truncated, or a malformed/nonfinite value that raised
a raw Python conversion exception. A malformed threshold could also raise
an exception for a non-duration outcome even though that threshold is unused.

Direct callers now receive `EventLogWorkspaceError` **before querying saved
logs** for supplied noninteger, boolean, nonfinite or out-of-range thresholds,
and for noninteger/boolean limits. The duration outcome still requires an
explicit threshold; integer limits retain their historical direct-call
1–100 clamp (REST continues to validate 1–100). A valid supplied threshold
for a non-duration outcome remains accepted but does not affect that outcome.
No statistical computation, feature attribution, confidence interval,
response field, HTTP parameter, REST/MCP/dashboard integration, permission,
retention or consent behavior changes.

Synthetic parameterized regressions cover early rejection, unused malformed
thresholds, inclusive integer threshold boundaries and existing integer limit
clamping. These checks are **not** native cross-platform CI or evidence of
real-provider/customer acceptance, causal relationships or authorized actions.

## Related R6 KPI automation-resource list validation

The KPI REST route passes literal resource names as a list. Direct Python
`kpis(automation_resources=...)` previously iterated a single string into
individual character names, could accept malformed mixed-type entries, and
could raise uncaught `TypeError` for noniterable or unhashable values.

Direct calls now accept `None` or a `list[str]` and reject other shapes or
non-string entries with `EventLogWorkspaceError` **before querying a saved
log**. Valid literal names (including leading/trailing whitespace), duplicate
deduplication in first-seen order, omission of empty strings, the 50-distinct-
resource limit, and the 200-character-per-name limit remain unchanged. The
existing REST query parsing and response shape are not changed. Synthetic
mocked-query regressions exercise malformed inputs, empty/duplicate/whitespace
names, and boundary-sized valid lists. These are not native CI, live-provider
acceptance, or consenting-customer evidence; no resource name establishes a
verified automation outcome or authorizes an agent action.


## Related R6 root-cause outcome-name validation

The direct Python `root_cause(outcome=...)` entry point previously checked
membership in a set of supported outcome names before checking the input type.
A list, dictionary or set is unhashable, so malformed direct calls could raise
raw Python `TypeError` rather than the documented `EventLogWorkspaceError`.
The entry point now requires a string belonging to the existing four outcomes
(`duration_over`, `contains_activity`, `ends_with`, `rework`) before
querying any saved log. The error message and valid names are unchanged.

Synthetic mocked-query tests cover unhashable and other malformed values,
plus all four valid names with their required parameters. This does not change
statistics, thresholds, returned fields, authenticated REST/MCP/dashboard
contracts, consent, retention, tenant isolation, provenance or agent-action
authorization. Native cross-platform CI and consenting-provider/customer
acceptance remain separate required gates.
