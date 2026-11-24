# Paired decision regression across saved policy revisions (R21)

In the local process catalog, open a saved revision, load its revision
history, compare an older saved revision with the opened one, and expand
**Test behavioral regression between saved revisions**. Enter a reviewed
case suite. The two models receive the exact same case facts and explicit
calendar date. Independently specify the expected decision status and
selected target (when applicable) in each revision.

Use synthetic or explicitly authorized local data; never enter unnecessary
customer facts. The editor accepts an object with a single `cases` field:

```json
{"cases":[{"case_id":"case_1","as_of_date":"2026-10-26",
 "facts":{"amount":150},
 "expectations":[{"table_id":"purchase",
 "before":{"status":"unique_match","target_activity_id":"escalate"},
 "after":{"status":"unique_match","target_activity_id":"accept"}}]}]}
```

The checkbox explicitly authorizes local case-fact analysis and declares
that the expectations were reviewed. It does not independently attest their
correctness. Changing the facts invalidates results and clears consent.

## Comparison semantics

The existing decision evaluator, including required decision dependencies,
effective dates, UNIQUE and FIRST semantics, evaluates both versions. Tests
match stable table IDs within one workflow. Missing tables must have an
explicit `table_missing` expectation. Targets must exist in the corresponding
table. A changed decision activity ID or an added/removed table is flagged
as discontinuous identity. Unchanged IDs do not establish semantic identity.

Each reviewed table/case pair reports exact status/target comparisons and
classifies unchanged expectations as stable pass, regression, improvement,
or persistent mismatch. Changed expectations are classified separately,
including intentional changes that match both versions. There is no automatic
reward for changing an expectation to match observed behavior.

Other table/case pairs are counted as unreviewed, with a count of changed
behavior; they never count as passes. This is finite reviewed-case coverage,
not exhaustive policy verification, a causal result, proof of customer
outcomes, or authorization to execute. The reviewer should supply
representative boundary, exception, missing-data and dependency cases.
Do not treat post-hoc edits to expectations as independent validation.

## API and bounds

POST /v1/process-catalog/policy-regression takes query parameters
`workflow_id`, `before_revision`, `after_revision`, `before_sha256`,
`after_sha256`. Revisions must be ordered and belong to the same workflow.
The body adds `reviewed_by_user: true` to the example above. Requires a
local dashboard session and `X-OWG-Case-Facts-Authorized: true`.
The strict 128 KiB JSON parser rejects repeated keys and nonfinite values.
At most 50 unique opaque case labels and 600 explicit table expectations;
the existing bounded-facts validation applies. Both exact revision hashes
are rechecked before returning the no-store response.

No raw input facts, provider calls, new persistent store, rule approval,
or agent invocation are involved. The minimized report includes case labels,
dates, actual/expected statuses, target IDs, per-table and overall counts
and a deterministic suite digest. Treat even the minimized download as
potentially sensitive: decision statuses can disclose information about
the supplied facts. The digest is not encryption or a signature.

Regression fixtures cover changed thresholds, stable and intentional
changes, expired and unknown inputs, removed tables, invalid expectations,
immutable revision pins, authorization and strict JSON. Full execution
and browser acceptance must pass before release.


## Integrated change review

The dashboard requests `include_change_review=true` on the regression
endpoint. The response adds a `change_review` object containing bounded
structural marking summaries for both revisions, declared policy impact
with downstream/dependency witnesses, and coverage of every potentially
affected table by the supplied reviewed cases. Affected tables include
tables attached to reachable downstream activities as well as explicit
decision dependencies. Added and removed tables are included on their
respective sides.

The report separates known findings (after expectation mismatch, observed
structural completion problems, or unreviewed changed behavior) from
unknowns (incomplete structural exploration, incomplete affected-table
case review, unreferenced rules and changed manual/outcome expectations).
A structural completion witness does not evaluate predicates. Complete
case coverage refers only to the finite submitted cases and their
explicit expectations; it is not representative or exhaustive coverage.
A review with no listed findings does not approve a release, authorize
execution or establish actual policy compliance or business outcomes.

The original regression response remains available without the optional
query parameter. All checks reuse the same two pinned revisions, and no
case facts are returned or retained. The combined report adds bounded
structural witnesses and declared policy metadata; review the download
before sharing.

This release consolidates the previously separate structural assurance,
finite decision-domain coverage, policy impact and paired regression
features into one integration PR. The cross-feature HTTP acceptance test
opens two exact saved revisions and exercises all four endpoint families
in the same local app process.
