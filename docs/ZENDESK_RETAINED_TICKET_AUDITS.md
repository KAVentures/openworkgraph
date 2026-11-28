# Zendesk retained selected-ticket audit feed

Refs #1572, #326 and #370. Extends the existing selected-ticket preview to recurring retained ingestion in the common enterprise engine.

## Configure

Connect Zendesk using the existing OAuth flow with exactly the read scope and a renewable grant. Use a dedicated approved agent identity, exact tenant host and one to four selected numeric ticket IDs. In Enterprise connectors, choose the reviewed fields (status, priority, type, assignee_id, group_id), initial timestamp, advancing window, overlap and reporting lag. Approve transient full audit and agent metadata reads separately from local retention. Saving a profile does not call Zendesk.

The adapter retains fixed status, priority, type and assignment-action labels, UTC timestamps, and profile-scoped pseudonyms for events, cases and actors. Assignment values, previous values, comments, email, descriptions and other audit payloads are not retained by this adapter. The provider may return these transiently; do not approve sensitive tickets without appropriate authority. A deleted or inaccessible selected ticket fails the entire window rather than silently advancing a checkpoint.

## Run and schedule

Each run uses the existing renewable OAuth grant and checks the configured active agent identity before and after the selected-ticket audit reads. It requests offset pages from the fixed per-ticket audit endpoint, validates count and next-page metadata, and reconstructs every request rather than following provider URLs. Two scans must agree on audit IDs and the minimized projected event population. Duplicate audit IDs, malformed selected values, changed projections, missing pages and over-budget results refuse retention.

The bounded reader supports up to 500 audits per selected ticket, subject to tighter shared request, time, byte and child-event budgets. The source endpoint returns historical audits, so a shorter advancing window does not necessarily reduce upstream response size. Very large ticket histories require a separate larger-scale ingestion design. Repeat-read agreement is not a transactional source snapshot or proof of historical completeness.

The common engine atomically appends retained events and advances the checkpoint, deduplicates replayed IDs, records admitted run history and supports recurring local and Gateway jobs and governed snapshots. Existing operational tuning, onboarding and integrity controls apply. Private native crosswalks are not implemented for this adapter; no account-wide export, comment ingestion, source write, deletion reconciliation or live customer acceptance is claimed.

## References

- https://developer.zendesk.com/api-reference/ticketing/tickets/ticket_audits/
- https://developer.zendesk.com/api-reference/ticketing/users/users/
- https://developer.zendesk.com/api-reference/introduction/pagination/

Regression fixtures cover the actual retained transaction with synthetic provider responses, exact identity/scope checks, pagination, overlap replay, redaction and refusal of partial windows. Focused enterprise CI includes the new test file. Verify tenant OAuth, audit permissions and real API response behavior in a sandbox before release.
