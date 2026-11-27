# Reviewed operational follow-up packets

Refs #1572, #1607 and #1627. A reviewed or deferred evidence exception with an actionable disposition can be converted into a bounded internal follow-up request. These are human workflow records, not automatically dispatched tasks, external agent execution authority or independently verified remediation.

## Operator workflow

Select up to 50 reviewed or deferred exceptions in the Gateway owner triage console. Preview exact item revisions, evidence outcomes, dispositions and proposed fixed action codes. Source gaps map to source-coverage inspection; process follow-up maps to investigation; timestamp uncertainty maps to time-assumption review; scope/rule issues map to scope review; other reviewed reasons map to evidence review. Expected sample variation is not actionable through this workflow.

Approve the exact proposal and acknowledge the absence of external execution authority. Creating a packet is transactional, increments the queue revision, and does not change item disposition or underlying evidence. Up to 50 packets are retained per queue. The original evidence grants, expiry and queue deletion lifecycle apply.

A packet can progress requested -> in_progress -> completed_unverified, or be cancelled before completion. Status is a human report, not independent evidence that a source record changed or a process was corrected. A changed item disposition/revision makes the entire pinned packet stale; a stale outstanding packet can be cancelled but not progressed or exported. Unrelated queue changes require refreshed queue revisions. Current active duplicate requests for the same items are refused. Status history is bounded. An explicitly approved JSON export includes pseudonymous linked cases, rule IDs, fixed action codes and digest provenance, not source event rows or credentials. Downloaded copies cannot be recalled by grant revocation.

No provider request, write, ticket creation, notification, assignment, scheduled enforcement or agent execution is performed. A separate verified implementation/runtime workflow is necessary for automated remediation.

## API

All endpoints are owner-only under /v1/admin/connector-events/{organization_id}/exception-queues/{queue_id}/followups. GET lists packets; POST /preview reviews item pins, POST on the collection creates a reviewed packet, and POST /{packet_id}/change and /{packet_id}/export use exact queue and packet revisions plus separate explicit consent.

## Verification

The full integrated evidence journey and route precedence fixtures are included, along with follow-up lifecycle, stale item, concurrency, grant expiry and queue deletion tests. The shared quality computation uses the same contract as the offline verifier. Browser fixtures exercise creation, status changes, export and queue deletion. JavaScript parsing passed; runtime tests, CI and live customer acceptance remain outstanding.
