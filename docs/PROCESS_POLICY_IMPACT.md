# Saved process policy revision impact (R11/R21)

Open an immutable process revision in the local catalog, open revision
history, compare another revision of the same workflow, and select
**Review policy change impact**. The report compares saved revisions only,
not editor drafts, live source state, or independent policy approval.

## Change categories

Stable rule IDs identify additions, removals, text changes, confirmation
changes, changed structured predicates, and reusable rule-library reference
changes. Structured predicates are normalized for comparison: all/any
condition ordering and duplicates, membership ordering and duplicates, and
equivalent numeric representations do not change the normalized declaration.
Booleans remain distinct from numbers. A stored representation change is
reported separately. This limited normalization does not prove arbitrary
logical equivalence; text changes may change requirements even when the
structured predicate stays the same.

Decision tables are compared by stable IDs, row membership and bindings,
hit policy, row order (FIRST versus UNIQUE), effective dates, source basis,
confirmation, and required-decision fact bindings and order.
Transition additions/removals and rule rebindings, manual review condition
changes and terminal outcome expectation changes are listed separately.

## Potential impact

For each saved revision independently, changed rules identify their direct
guarded edges and decision tables. Changed tables and tables attached to
changed outgoing edges are additional starting points. Required-decision
dependencies are traversed from upstream to downstream, returning one
deterministic shortest witness per reachable table. Graph traversal from
affected decision activities, branch targets, transition endpoints, manual
review activities and outcome terminals returns one shortest witness per
reachable activity and the affected terminal IDs.

These are conservative structural reachability witnesses, not executable
routes. A loop, infeasible branch, unchanged predicate, or token
synchronization may prevent a particular execution. No route frequency,
business outcome, factual rule compliance or source state is inferred.
Unreferenced changed rules are explicitly listed. No previous review is
transferred to a new revision. Activity labels and implementation-only
changes remain in the existing general revision diff, not this policy
comparison.

## API

`GET /v1/process-catalog/policy-impact` requires a local dashboard session
and five query parameters: workflow_id, before_revision, after_revision,
before_sha256, after_sha256. Both revision numbers must differ, and both
content digests must match the same workflow's saved snapshots. Missing
revisions refuse the request; digest mismatch is HTTP 409. Both references
are rechecked before response. The route is read-only, no-store and makes
no provider calls. The browser discards responses after the comparison
context changes. Downloading the minimized JSON is explicit.

The report contains IDs, fact paths, change categories and witness paths
rather than full rule texts or expected values. It is not an anonymized
document; identifiers and manual-review labels may still be sensitive.
No policy, process model, approval, evidence, or execution permissions are
changed or added. Synthetic tests cover dependency propagation, exact
normalization, FIRST row ordering, rule rebinding and local authorization.
