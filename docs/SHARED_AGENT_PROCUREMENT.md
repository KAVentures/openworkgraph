# Shared organization AI procurement

The enterprise process portal now supports a tenant-scoped procurement
portfolio and per-process assessment history. It uses existing human identity,
process permissions, exact reviewed design revision and workflow knowledge
storage. No new execution service or source access is introduced.

1. Export the current independently reviewed shared process. When using
   the offline evaluator, include organization_catalog_reference with
   organization_id from the shared catalog in the process export, exactly
   as the generated agent project does.
2. Create discovery and holdout trial plans, collect minimized captures,
   nominate a candidate, and assemble the assessment bundle described in
   AGENT_PROCUREMENT_SELECTION.md (discovery_plan, discovery_captures,
   holdout_plan, holdout_captures, nomination).
3. As a process owner/editor with write scope, open the process in the
   enterprise portal, load shared procurement review, select the bundle
   and explicitly confirm organization sharing. The Gateway recomputes
   the assessment against its own shared revision. Only aggregate checks,
   candidate labels/configuration hash, policy and evidence fingerprints
   are stored, not the bundle or case rows.
4. A different authenticated process reviewer/owner/admin with review
   scope selects the same bundle and records approval for rehearsal
   review only, retain baseline, or defer. The Gateway recomputes the
   assessment and checks both current access and procurement revisions.
   A failed or incomplete holdout cannot receive positive approval.
5. Load the portfolio for a paginated view of current decisions and
   blockers; select a process to inspect details and export its minimized
   summary. Changed design or access invalidates effective approval.
   Resubmit new evidence rather than silently editing an old decision.

API: GET /v1/human/process-procurement-portfolio?after=&limit=50;
GET/POST /v1/human/processes/{workflow_id}/procurement;
POST .../procurement/decision and .../procurement/delete.
Submit fields: process_revision, expected_procurement_revision,
expected_access_revision, bundle (JSON string), user_confirmed,
organization_sharing_confirmed. Decision fields:
expected_procurement_revision, expected_access_revision,
expected_assessment_sha256, decision (approve_rehearsal_review,
retain_baseline, defer), bundle (same JSON string), user_confirmed.
Only explicit owner/admin deletion removes all procurement revisions,
and deletion of the parent process also erases that history. Limit 30
revisions; no automatic pruning. Audit entries record only identifiers
and decision metadata. All operations require delegated human scope;
no service or evidence-only token fallback.

Authenticated actor separation does not independently prove real-person
identity or trustworthy evaluation provenance. Submitted trial evidence,
costs, case independence and business outcomes are not independently
verified. Approval is for further rehearsal review, not purchasing,
execution, production rollout, or a change to any deployment. Portfolio
does not sum savings or compare incomparable process populations.
