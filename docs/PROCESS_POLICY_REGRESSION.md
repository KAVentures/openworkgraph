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
