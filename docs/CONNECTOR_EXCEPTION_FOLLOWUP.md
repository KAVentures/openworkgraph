# Human evidence exception follow-up

Advances #1572 and #370. The organization owner can create, start, report completion of, or cancel a follow-up on an already reviewed or deferred evidence exception. Follow-ups are not automated remediation, independently verified process improvement, or evidence of source repair.

## Review and plan

Open the grant-bound exception triage queue, select reviewed or deferred items with actionable dispositions, choose Plan follow-up, and enter an integer due interval in hours. The interval must end strictly before the underlying queue expires. The action is derived from the exact current disposition:

- Source gap suspected: inspect source coverage.
- Process follow-up needed: review the process with its owner.
- Timestamp uncertainty: review timestamp assumptions.
- Scope or rule needs review: review scope and rule.
- Other reviewed reason: review the other evidence issue.

Expected sample variation does not generate a follow-up. Neither open nor investigating exceptions can generate a plan until an appropriate disposition is recorded. An existing active plan must be completed, cancelled or invalidated before a replacement can be created.

Select items and separately approve each batch transition. A planned follow-up may start, be reported done, or be cancelled; an in-progress follow-up may be reported done or cancelled. Reported completion is an operator assertion. Changing any disposition invalidates an existing plan, even if the previous disposition is later restored. Review and plan again. The immutable evidence outcome and human disposition are not changed by follow-up transitions.

## Worklist and retention

The dashboard displays per-item follow-up state, due time and staleness. Counts-only review includes planned, in progress, reported done, cancelled, stale and suggested-unplanned items. Overdue is an overlapping count of non-stale open plans; it is calculated from the Gateway clock and does not trigger a write or notification. The separately approved full JSON worklist is pinned to the current queue revision, sorted by overdue and due time, and includes a digest, fixed action codes and opaque item IDs. It omits linked-case IDs, raw event rows, native IDs, activity labels and free-text notes. This export may still contain sensitive metadata and is not anonymous.

The plan lives inside the existing grant-bound exception item. Up to 50 item edits are atomically applied using both queue and item revisions; any invalid edit rejects the batch. Bounded follow-up history is retained per item. Publication expiry/revocation or queue deletion makes follow-up records inaccessible and existing cleanup removes them. Previously downloaded files are not recalled. No provider calls, source changes, assignments, notifications, automated scheduling or verified completion are implied.

## API

Owner-only routes under /v1/admin/connector-events/{organization_id}/exception-queues/{queue_id}/followups:
- GET: counts-only current summary.
- POST /change: expected_queue_revision, edits containing item_id, expected_revision, operation and (for planning only) due_hours, confirm_followup.
- POST /export: expected_revision and confirm_followup_export.

## Acceptance

The regression suite exercises real Gateway queue fixtures, exact revision and consent fences, batch rollback, grant expiry and revocation, stale plans, overdue observations and preservation of evidence outcomes. Chromium fixtures exercise plan, reported completion and worklist download. The consolidated evidence-journey acceptance additionally transfers actual synthetic Gateway downloads into an independent local mining browser; no live provider requests or customer data. Full CI and live deployment acceptance are still required.
