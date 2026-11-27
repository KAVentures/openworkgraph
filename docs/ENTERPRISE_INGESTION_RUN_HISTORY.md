# Enterprise ingestion run history

Refs #1572, #326 and #370 R3/R20. The retained connector runtime previously exposed its latest result only. This journal retains a bounded, counts-only record of admitted local ingestion attempts across the shared enterprise adapter registry, without creating another event store.

## Operator workflow

Open Enterprise connector operations and select **Ingestion run history**. Filter by exact profile ID, admitted local lookback (1–90 days), outcome and maximum returned rows (1–1,000). Review the committed event additions, duplicates, checkpoint transitions, elapsed local attempt duration, failure codes and retry cooldown. Export a fresh JSON result if needed for troubleshooting. The summary describes the returned rows only; a truncation flag and matching retained population distinguish an incomplete view from an exhaustive one. The view and export do not contact providers or renew credentials.

Unknown metrics remain unknown, rather than zero. Counts are sums of the reported values for successful attempts, with explicit numbers of contributing and unknown successful attempts. Failed attempts have no fabricated event counts. Reported BigQuery bytes are not a complete billing report. Elapsed time is local wall-clock attempt time, not provider execution time or independent performance measurement.

## Transaction contract

An admitted attempt receives its journal row in the same transaction as the local profile lease. The requested change window, previous checkpoint, profile revision and provider are pinned to this attempt. Dispatches refused before admission (including a retry cooldown, disabled profile or no new window) do not generate invented read attempts.

A successful attempt updates the journal in the same transaction as the canonical event-log import or append and checkpoint. Empty windows also commit the checkpoint and terminal receipt together. Failure receipts and profile state changes commit atomically; an exception after an already committed success cannot rewrite that receipt. The journal stores only allowlisted nonnegative integer counts and bounded internal error codes, never activity labels, credentials, response bodies, native source IDs or event payloads.

An expired running lease is displayed as interrupted/unreconciled without mutating its stored state. On an admitted replacement attempt, the previous lease is marked interrupted in the same transaction. An explicit profile revision change marks the admitted attempt invalidated. Neither state proves a remote operation was cancelled. A late outcome from a fenced run cannot overwrite the terminal receipt. Pre-upgrade attempts and legacy in-flight intents without a journal row are not reconstructed.

## Lifecycle

The newest 200 terminal attempts per profile are retained plus any active intent. The journal is erased with the profile or its retained dataset. This does not erase downloaded exports or backups. The 90-day query lookback is not a storage expiry guarantee. This operational history is local, not a centrally collected fleet audit trail, immutable compliance archive, or independent provider-health monitor. Existing Gateway job receipts remain separate.

## API

GET /v1/enterprise-connectors/run-history supports optional profile_id, days, limit and state filters. It requires the existing human dashboard session. Responses use the format owg_enterprise_ingestion_run_history_v1. No source request is authorized by querying history.

## Verification

Regression fixtures cover retained event transactions, overlap deduplication, empty windows, cooldown, expired and replaced intents, explicit revision invalidation, late failures, transaction rollback, bounded history, deletion and no-read review. The enterprise-stack acceptance workflow includes the new test file. CI and live operational acceptance are separate gates; no customer-provider validation is claimed.
