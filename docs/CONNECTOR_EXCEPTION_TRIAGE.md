# Enterprise evidence exception triage

Follow-up to #1605 (linked conformance) and enterprise delivery #1572.
This workflow turns selected reported-evidence exceptions into a persistent,
owner-reviewed investigation queue. It is not a compliance determination,
automated remediation system or personnel assessment.

## Operator workflow

1. Review exact published snapshots and linked cases in the existing
   cross-source review screen. Define and evaluate conformance rules.
2. Approve retention of the exact reviewed exception list and create the
   queue. The Gateway recomputes the entire conformance worklist under
   the same organization transaction as queue creation. A passing report
   without exceptions cannot create a queue. Repeating the same digest
   reopens the existing queue rather than creating duplicate work.
3. Open **Evidence exception triage**. Review the current revision, state
   counts and paginated items. Select up to 50 items and explicitly
   authorize the new state. Open or investigating requires no reason;
   reviewed or deferred requires one of six fixed disposition reasons.
   Review states are independent of the original evidence outcome, which
   is never changed by a disposition. Reopen an item when needed.
4. Review the recent history (ten transitions per item). Each transition
   records prior state/reason, new state/reason, revision and timestamp.
   The existing Gateway audit log records the authenticated owner and
   counts/digests for queue operations, not individual case or activity
   values. This is not a complete per-item attribution ledger.
5. Separately approve a full current-revision disposition CSV download.
   It contains original evidence outcomes, current dispositions, item
   and queue revisions and digest provenance. No raw event rows, native
   case IDs, arbitrary activity labels or free-text notes are exported.
   Preserve the original rule report separately for interpretation.
6. Delete the queue when review is no longer needed. Its source publications
   and local logs remain intact. A previously downloaded CSV cannot be
   recalled.

## Exact scope and lifecycle

A queue contains only non-satisfied and indeterminate rule observations
from the exact reviewed conformance digest. A new review digest requires a
new queue; old dispositions do not automatically migrate. Linked case
pseudonyms are scope-specific, not stable enterprise business IDs.
An exception is an observation to investigate: missing events may reflect
source visibility, sample boundaries, delayed evidence or unsuitable rules.
Reviewed means a human disposition, not a verified resolution.

Queue creation is atomic with exact publication and conformance
recomputation. Subsequent operations require the original exact grant,
publication revision and manifest digest to remain live. A revoked,
replaced or expired publication invalidates the entire queue immediately
for access; the existing retention sweep purges invalid queue data, with
a bounded organization batch every 60 seconds. Cleanup failures are exposed
in the inventory. Backups and prior exports are subject to deployment
retention policy and are not erased by this workflow.

Edits require an exact queue revision and exact revision for every selected
item. Any missing, stale or unchanged edit rejects the whole batch. A
successful batch advances the queue revision once and each changed item
revision once. Pagination requires the exact queue revision after page
one. Export and delete also require the exact queue revision. All operations
use the existing SQLite/PostgreSQL organization fence. No worker tokens
or ordinary non-owner administrator sessions may access the queues.

## Retention and bounds

At most ten active queues and 40,000 retained items per organization;
at most 20,000 items per queue, 50 edits per transaction, 100 items per
API page (50 in the console), and ten prior transitions per item.
Queue creation inserts rows in bounded batches. Definitions retain
exact publication pins, conformance/review digests, counts, fixed
disposition codes, opaque case and item identifiers and safe rule IDs.
Rule IDs are operator-defined identifiers and should not contain personal
information. Rules, activity labels and source case membership are not
retained in the queue. No new free-text notes, assignments, notifications,
automatic escalation or scheduled conformance execution are implemented.

## API

All endpoints are owner-only under
/v1/admin/connector-events/{organization_id}/exception-queues:

- GET: list active queues and state counts.
- POST: full unfiltered exact conformance review, expected conformance
  digest and confirm_exception_retention.
- POST /{queue_id}/page: state, offset, limit, expected_revision.
- POST /{queue_id}/change: expected_queue_revision, edits (item_id,
  expected_revision, state, reason), confirm_disposition.
- POST /{queue_id}/export: expected_revision, confirm_disposition_export.
  Returns CSV with X-OWG-Disposition-SHA256.
- POST /{queue_id}/delete: expected_revision, confirm_delete.

## Validation

Regression fixtures cover actual published evidence, exact recomputation,
idempotent creation, state transitions, stale revisions, concurrent
edit fencing, atomic rejection, consent, history bounds, tenant isolation,
revocation/expiry cleanup, disposition export and owner-only authorization.
Chromium exercises queue creation and repeat-save, investigation, review,
history, export and deletion. The focused draft workflow is extended
to exercise SQLite/PostgreSQL and Chromium. Full local checkout failed
because github.com DNS resolution is unavailable in the authoring
environment. The integrated tests have not been executed locally.
Land after #1605 and #1589 validation, reconciling overlapping routes,
review UI, browser fixtures and workflows with other follow-ups.
