# R26 monitored deployment readiness

The local read-only bridge uses the existing R18 Automation Monitoring release
inventory and retained run/step records. It creates no deployment, changes no
release, pauses no runner and grants no rollout permission.

Before the preceding sandbox rollout-review decision, create a structured
rollback document. Use its canonical digest (shared.agent_procurement.digest)
as rollback_plan_sha256 in that decision. Example:

```json
{
  "format": "openworkgraph.procurement-rollback-plan.v1",
  "baseline": {
    "deployment_id": "dep_0123456789abcdef",
    "release_revision": 1,
    "manifest_sha256": "REPLACE_WITH_BASELINE_MANIFEST"
  },
  "candidate": {
    "deployment_id": "dep_fedcba9876543210",
    "release_revision": 1,
    "manifest_sha256": "REPLACE_WITH_CANDIDATE_MANIFEST"
  },
  "baseline_configuration_sha256": "REPLACE_WITH_BASELINE_CONFIGURATION",
  "candidate_configuration_sha256": "REPLACE_WITH_CANDIDATE_CONFIGURATION",
  "owner_alias": "rollback-owner",
  "stop_candidate_before_restore": true,
  "reconcile_inflight_work_before_restore": true,
  "preserve_evidence": true,
  "require_separate_execution_authorization": true
}
```

Use actual IDs and hashes. In R18 release inventory explicitly declare
the respective manifest digest as artifact_sha256; this is a human
declaration, not attestation of deployed bytes. Both deployments must
identify the exact evaluated saved process and separate current release
revisions. Baseline stage must be reported pilot or production; candidate
must be reported pilot for a positive referral (shadow can be inspected).

Select **Review monitored deployment readiness** in the Process catalog.
Supply one JSON bundle containing exactly rollout_bundle (the original
outcome decision bundle), rollback_document, and request, whose fields are
expected_rollout_receipt_sha256, baseline and candidate (the same pins
as above), window_start and window_end (timezone-aware ISO timestamps),
and policy. Policy fields and defaults:

- minimum_baseline_runs and minimum_candidate_runs: 20 each
- minimum_candidate_success_basis_points: 9500
- maximum_candidate_failure_basis_points: 0
- maximum_candidate_handoff_basis_points: 500
- maximum_success_drop_basis_points: 0
- maximum_candidate_p95_ratio_basis_points: null (optional)
- require_all_terminal_durations: true

The common exact half-open window must end in the past, span at most 90
days and include all retained runs starting in it. Missing attribution
and running reports block referral. No sampling; caps 50,000 runs and
2 million steps per arm. Rate and ratio gates use integer comparisons.
A deterministic run and step population fingerprint covers all selected
retained rows without exporting their raw IDs or text. Both deployments
are captured under the existing monitoring lock.

Select **Record deployment review referral** with the same bundle plus
decision containing decision (refer_to_release_owner, retain_baseline or
defer), reviewer_alias, expected_deployment_review_sha256 and confirmed:true.
Current evidence is recalculated and stale evidence refused. A distinct
declared reviewer alias and every passing required gate are required for
positive referral. Dashboard session only; no-store and no persistence.
The latest process revision is required. No release record is mutated.

Retained reports do not prove independent outcomes, comparable case
populations, actual runtime identity or deployed bytes. Rollback target
identities and prerequisites are pinned, not executed or tested. The
receipt requests a separate human release-owner review, not production
authorization. Existing R17/R18 controls must be used separately.
