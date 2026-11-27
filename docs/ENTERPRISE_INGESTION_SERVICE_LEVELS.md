# Enterprise ingestion service levels and alert inbox

Refs #1572, #326 and #370. Administrators can monitor local retained ingestion against explicitly approved per-profile service-level thresholds. This is local operational monitoring, not a provider availability SLA, uptime monitor, source completeness assertion or remote notification service.

## Configure and operate

Open Enterprise connectors and choose **Ingestion service levels and alert inbox**. Load profiles, choose an exact profile revision and save a reviewed policy:

- Success age: threshold in hours since the most recent committed local success.
- Checkpoint age: threshold in hours since the recorded checkpoint.
- Consecutive failures: number of consecutive failed admitted terminal attempts in the current profile revision.
- Expired intent: the local admitted lease has expired without reconciliation.
- Integrity hold: a reviewed local containment marker is present.

For missing success/checkpoint timestamps, the age clock starts at initial profile creation. A future or malformed clock value remains unknown. Disabled profiles suspend age and failure-streak observations; existing active episodes remain open rather than being incorrectly classified as recovered. Expired intent and integrity hold checks remain independently observable.

Choose an evaluation cadence of 5–1,440 minutes and explicitly enable recurring local evaluation. The existing desktop scheduler checks due policies while it runs; there is no guarantee of timing while the machine is offline, sleeping, or busy with other jobs. Manual **Evaluate enabled policies now** works without waiting for a due time. Loading the inbox never evaluates or mutates a policy. Monitoring performs no provider calls, credential renewal, dispatch, or external notification. It is an independent opt-in, not permission to run a source feed.

Policies follow the same immutable local profile ID across later profile revisions. Saving requires exact current profile and policy revisions. Replacing or disabling a policy closes its previous active episodes as policy_replaced, not observed_clear. The new policy starts its own observations. Local profile or dataset deletion erases its monitoring policy and episodes. This does not revoke downloaded copies or remote artifacts.

## Episode semantics

One active episode per profile and signal. Repeated breaches update the last observation without creating another alert or discarding an acknowledgement. Unknown observations preserve active episodes; an observed clear condition closes the episode. A later breach opens a new episode with a fresh ID. Acknowledgement is an explicit version-pinned mutation that does not change the underlying observation or close an active alert. There is no automatic remedial source action.

Failure streaks use the current profile revision's newest admitted terminal attempts in the bounded run journal. Success breaks the streak; interrupted or invalidated attempts are unknown rather than being falsely counted as provider failure or success. Refused pre-admission jobs are not represented. A missing historical streak is unknown. The inbox retains the newest 100 closed episodes per profile plus active episodes. Retention is count-bounded, not a statutory archival guarantee.

Each evaluation transaction atomically writes a policy's observation and episode updates; failures roll back that profile without rolling back independently evaluated profiles. The scheduler retries due policies on later ticks. Clock regression refuses a policy evaluation rather than overwriting later observations. Errors in the explicit manual evaluation response are counts-only; reload to review successful committed evaluations. The inbox does not independently establish external source health.

## Local API

- GET /v1/enterprise-connectors/service-levels: profile policies, observations and bounded alert episodes.
- POST /v1/enterprise-connectors/service-levels/save: profile_id, exact profile_revision, expected_rule_revision (zero for a new policy), enabled, interval_minutes, optional success_age_hours, checkpoint_age_hours, failure_streak, watch_expired_intent, watch_integrity_hold and confirm_local_monitoring.
- POST /v1/enterprise-connectors/service-levels/evaluate: explicit confirm_local_evaluation=true; evaluates enabled policies.
- POST /v1/enterprise-connectors/service-levels/acknowledge: episode_id, version and confirm_acknowledgement.

All endpoints use the existing human dashboard session. No AI bearer or external execution authority is granted.

## Verification

Regression tests use actual retained workspace transactions and synthetic source transport to cover due evaluation, acknowledgement, recovery/new episode, unknown observations, admitted failure streaks, expired intent/hold distinctions, stale policy/profile revisions, rollback, clock regression, erasure and no-provider monitoring. Focused enterprise stack CI includes the regression module. Live operational acceptance and end-to-end scheduler behavior on deployed hosts remain separate gates.
