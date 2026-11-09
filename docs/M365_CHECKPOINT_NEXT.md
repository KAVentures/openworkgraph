# Microsoft 365 checkpoint-anchored next extraction

Run `python -m server.m365_outlook_checkpoint_next --help` for required
scope, grant, private artifact paths and explicit approvals.

This **opt-in** command permits the next bounded Microsoft Graph delta GET
only after a previously committed local checkpoint matches the exact scoped
candidate cursor, CSV digest and log identity. It reuses the existing
authenticated saved-event membership gate, previously reviewed handoff and
current provider grant. Output is a new candidate for human review, **not**
an automatic OWG append or checkpoint commit. Every run requires separate
local read, Graph read, local export and removal review flags. The token is
supplied only through `OWG_M365_GRAPH_TOKEN`, and the local authenticated
dashboard session through `OWG_DASHBOARD_SESSION`.

A missing, revoked, stale or mismatched predecessor fails closed. New output
paths must be distinct and private. A refusal or uncertain network outcome
must be inspected and reconciled manually, never blindly retried. The
checkpoint ledger is local evidence of operator approval, **not** a provider
acknowledgment or proof of full mailbox completeness. This command neither
refreshes OAuth tokens nor verifies a real Microsoft 365 tenant. The source
remains read-only; provider deletions are review markers, not OWG erasures.

Acceptance: tests verify the strict membership delegate, no Graph invocation
on stale/uncommitted state, and explicit consent requirements. Real tenant,
rate-limit and crash recovery acceptance remain outstanding.
