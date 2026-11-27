# Reviewed routine ingestion intent reconciliation

Advances #1572 and #370. The advancing ingestion path no longer silently replaces an expired routine lease. A local operator reviews the exact outstanding intent, preserves the last committed checkpoint and retained evidence, records an uncertain remote outcome, and disables the feed with a new revision. Historical recovery continues to use its distinct reconciliation workflow.

## Operator workflow

1. Open Enterprise connectors > Expired routine ingestion recovery and load local intents.
2. Select the affected profile and review its exact run identity, lease expiry, journal state, admission window and retained checkpoint.
3. Stop or reconcile competing workers, acknowledge that the remote outcome is uncertain, and approve disabling the feed and invalidating its previous revision.
4. Apply the review. The stored journal entry is marked interrupted and a reconciliation receipt is recorded in the same workspace transaction. Previously committed evidence, checkpoint, success receipt and request-coverage history are not changed.
5. Inspect the retained state and independently re-enable the feed and reauthorize dispatch. An old schedule or worker pin must not be reused.

A review expires after ten minutes. The exact private profile state and journal observation are bound to an authenticated review digest. Apply repeats the assessment under the workspace lock before changing the profile; active leases, stale or tampered plans and inconsistent journal/profile pairs are refused. Existing integrity holds are not removed. Legacy intents without a recorded admission may be reconciled, but no synthetic run history or window bounds are invented.

The workflow does not contact providers, renew credentials, cancel already-dispatched remote reads or establish their outcome. It does not repair corrupted journals or erase retained data. Ordinary state changes cannot bypass expired routine intent reconciliation; explicit integrity containment and deletion remain separate operations. Existing schedules are not changed by the reconciliation itself.

## API

All endpoints require the existing local dashboard session:

- GET /v1/enterprise-connectors/routine-reconciliation
- POST /v1/enterprise-connectors/routine-reconciliation/preview
- POST /v1/enterprise-connectors/routine-reconciliation/apply
- GET /v1/enterprise-connectors/routine-reconciliation/{profile_id}/receipts

The apply request includes the exact profile revision, run identity, review timestamp and digest, plus separate confirmations for the intent, competing workers, uncertain remote outcome and disabling/reauthorization. Receipts are bounded per profile and removed with profile or retained-dataset deletion.

## Validation

Regression fixtures cover retained checkpoints, stale worker fencing, passive inspection, routine/scheduled dispatch refusal, legacy admissions, journal inconsistency, active leases, review expiry, tampering, explicit approvals and transactional rollback. Existing interrupted-lease tests are updated to require reviewed reconciliation before a replacement run. The focused enterprise acceptance workflow includes the new test file. CI and live deployment acceptance remain required.
