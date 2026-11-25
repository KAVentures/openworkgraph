# R21 — Review and upgrade linked policy rules

The local Process catalog now supports reviewing and updating several linked
library rules together against an exact saved process revision. This extends
the reusable rule library without changing the normal review/save boundary.

## Operator journey

1. Open the latest saved process, and select **Review linked rule upgrades**.
2. Inspect the historical link, latest source revision, old and new rule text,
   predicate changes, and affected decision tables. A rule can be selected only
   when its original historical pin and local copy match and a newer library
   revision is available. Same-content newer revisions are shown separately.
3. Select the rules to upgrade. Nothing is selected by default. Open the
   selected upgrades as unsaved editor changes. All selected source revisions
   and content hashes must still match the preview; if one changed, the entire
   proposal is refused and must be refreshed.
4. Review the new rules and affected tables, then explicitly save a new process
   revision using the existing catalog. Source libraries and earlier process
   revisions are unchanged.

A changed rule is unconfirmed in the proposal. All decision tables directly
using the rule, and tables transitively depending on those tables, are marked
unconfirmed, including if the content is identical but the source revision
changes. Other confirmations remain unchanged. The existing single-rule link
also now clears these affected table confirmations. A rule without a structured
predicate cannot replace a decision-table rule if that would invalidate the
model; the entire proposal is refused.

Missing historical sets/rules, mismatched historical or latest content digests,
locally edited copies, and removed library rules are disclosed rather than
automatically overwritten. The existing drift report also detects invalid
historical pins. This is local declared provenance, not independent source
authentication or evidence that the policy is authoritative.

## API

Dashboard session only: POST /v1/process-catalog/rule-library/upgrade-review
with workflow_id and revision query parameters. Send an object containing
expected_content_sha256 for the saved process; omit selections for preview.
To construct the unsaved proposal, include selections containing 1–40 distinct
objects with rule_id, library_revision and content_sha256 from the preview.

The latest process revision must match, and each selected library revision is
checked against the latest snapshot read for the proposal. The response includes
affected table IDs, cleared confirmation IDs and the proposed model. Nothing
is saved by this endpoint. A later change after proposal creation does not
silently update the pinned source; check drift again before use. The normal
catalog save remains revision-guarded.

No rule is evaluated, executed or automatically approved; no provider is
contacted. This is not an enterprise shared rule library or a runtime policy
authorization mechanism.

Tests cover exact pins, stale selection refusal, atomic upgrades, transitive
decision review invalidation, authenticated HTTP and the browser review/save
journey. Full CI and real policy validation remain separate acceptance gates.

Related: [#370 R21](https://github.com/KAVentures/openworkgraph/issues/370).
