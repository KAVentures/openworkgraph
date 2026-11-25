# Incident run evidence (R18)

The automation monitoring incident inbox now supports exact current retained-run
review for reconciled failed-run and human-handoff incidents. Previously the
inbox showed only up to ten example IDs. Select **Inspect retained run evidence**
to page the entire matching cohort (20 per dashboard page), expand a run, and
select **Read exact retained report** for its ordered steps.

Membership uses the same first-problem-activity rule as reconciliation, including
the explicit no-step group. It is scoped to one deployment, one incident kind,
and the exact fingerprint; later failures in a different activity do not qualify.
The complete membership digest must match the incident record. Each page also
pins all retained report fields and steps, including undisplayed runs, alongside
the review revision and attached design digest. A changed run, incident review,
membership or attached design invalidates the pagination snapshot. The detail
reader uses the existing exact run digest. Refresh the incident list after
reconciliation if membership no longer matches.

The endpoint is session-only and returns no-store:
POST /v1/automation-monitoring/incidents/{incident_id}/evidence accepts
expected_last_seen, expected_review_revision, expected_membership_sha256,
optional expected_population_sha256, offset and limit (1–100). Subsequent pages
require the population digest. Limits are 50,000 matching runs and 2,000,000
matching steps; excess cohorts are refused, never silently sampled.

This is current retained evidence, not an immutable archive of past incidents,
a verified business outcome, or an independent runtime observation. Channel
incidents do not pretend to have run membership. A zero-current-run historical
incident remains visible. Existing triage is separate and continues to use its
existing optimistic revision/occurrence contract. Evidence review does not
authorize a retry, replay, or provider action.

Synthetic tests cover first-issue membership, non-sample paging, stale content,
unreconciled imports, review changes, zero membership, handoffs, strict requests,
dashboard authentication, bounded work, and exact report readback. Existing
signed-report browser acceptance exercises opening and reading evidence.
