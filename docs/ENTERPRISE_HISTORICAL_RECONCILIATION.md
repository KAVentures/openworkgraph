# Enterprise historical reconciliation — current integrated stack

Refs #1572, #1594, #1611 and #1614. This integrates the earlier historical recovery work with the expanded retained connector runtime. It does not modify the original recovery branch or active workers.

## Operator workflow

In Enterprise connectors, expand Historical window reconciliation for an enabled, credential-bound profile with a completed checkpoint and no integrity hold. Choose one exact, previously traversed half-open change interval within the original configured anchor and current checkpoint, no longer than the currently configured advancing window and within the age bound. Provider-specific cutoff and precision constraints apply. Preview binds the exact interval, revision, full private profile state, current checkpoint and receipt lineage. It performs no provider request or credential renewal. Review the current exclusion count and separately approve historical reads, retention and worker coordination. Stop competing source workers and pause or remove competing schedules as appropriate before dispatch.

The admitted historical read uses the normal adapter's fixed scope, existing source keys, provider budgets, rate-limit behavior and retention path. It may recover late events or deduplicate evidence previously retained. An empty successful window is also a valid committed request. Conflicting event payloads fail rather than overwrite existing evidence.

## Integrated retention contract

All 16 currently registered adapters are wired to historical reconciliation through their ordinary extractor and provider cutoff contract. Live acceptance of the expanded set is outstanding. The historical attempt shares the profile lease with ordinary ingestion. An expired historical intent requires explicit reconciliation and cannot be silently replaced by an advancing run. Revision changes and reviewed integrity holds invalidate historical intents; the recorded outcome remains uncertain rather than claiming remote cancellation.

Historical ingestion applies the current prospective case exclusions before append/import. Its receipt counts observed projected events and excluded projected events separately. The existing routine checkpoint, last attempt/success and routine outcome remain unchanged. The retained dataset ID may change from absent to present when the first late evidence arrives. SQL schema and Slack principal pins remain enforced by the shared runtime. Retry cooldown is honored.

The exact admitted historical interval is unioned into the durable committed-request coverage index in the same transaction as the retained events and historical receipt, without moving the routine cursor. A failed or rolled-back historical read cannot improve coverage. Successful historical reads may turn pre-upgrade unknown request intervals into recorded intervals. The total committed count includes historical successes, with a separate historical count. Detailed historical receipts are bounded and separate from the routine run journal. Replaying a matching retained receipt at the same profile revision makes no new provider request and does not increment coverage. Profile deletion or retained dataset deletion clears associated recovery state. Existing remote copies and backups remain separate.

## API and verification

The existing local human-only routes are POST /v1/enterprise-connectors/rest/{profile_id}/historical-preview, historical-execute and historical-reconcile. Preview accepts revision, since and until. Execute adds request_id, expected_plan_sha256, confirm_historical_read, confirm_retention and confirm_workers_reconciled. Reconciliation of an expired intent requires the exact request ID, revision, and confirmations that workers are stopped and uncertain outcome is accepted.

The earlier retained-workspace fixtures and real SQLite browser flow are integrated, with additional tests for exclusions, legacy unknown coverage, atomic rollback, integrity hold invalidation, stale receipt revision and adapter registration. Focused enterprise CI includes the regression module. Tests have not been executed locally. This is bounded exact-request recovery, not a proof of historical source completeness, correction/deletion reconciliation or a remote cancellation mechanism. The parallel service-level and fleet integration branches still require reconciliation; no claim of released enterprise functionality is made.
