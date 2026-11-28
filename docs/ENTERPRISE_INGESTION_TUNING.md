# Reviewed enterprise ingestion tuning

Refs #1572, #326 and #370 R3/R20. This local operator workflow recovers feeds from repeatedly oversized windows, large pages, reporting delay and late evidence without deleting an existing retained dataset or creating a new identity namespace.

## Workflow

1. Stop or reconcile local and Gateway worker activity, leases and pending receipts. Do not treat an expired lease as proof that an external operation was cancelled.
2. Open Enterprise connector operations and load **Reviewed ingestion tuning**.
3. Select up to 20 existing profiles. Edit only the supported numeric operational fields. Window, overlap, lag and page-size limits are provider-specific; SAP retains whole-day semantics, and Salesforce retains its minimum page size. Source credentials, catalog definitions, filters, selected records, mapping, initial anchor, identity and case notions cannot be changed here.
4. Preview the exact batch. Review the current and proposed settings, current checkpoint, retained population, illustrative next change windows and exact SHA-256 plan.
5. Approve the exact changes, separate dispatch reauthorization and worker reconciliation. Apply commits all selected profiles and a bounded audit receipt in one DuckDB transaction. If a single profile changes or becomes ineligible, the entire selection is refused.
6. Reauthorize local schedules and rebind Gateway jobs through their own reviewed flows. Existing pins refer to old profile revisions. A successful tuning apply does not silently authorize any new reads.

The next-window preview uses current time and provider-specific cutoff logic. It is illustrative, not a reservation or live provider capability check. A smaller advancing window may reduce query size, but does not guarantee a large overlap or unusually dense event window fits provider budgets. Increasing overlap may repeat more already-read evidence and create more upstream load; reducing overlap may miss late events. For historical gaps use the separate historical recovery workflow when available.

## Persistence and review contract

Each target is identified by its immutable profile ID and current revision. Preview validates the local credential binding and retains no new source data. The plan pins the complete private profile state and retained log metadata by digest; no private key, bearer, credential binding or raw event values appear in the response. The illustrative clock-dependent time projection is deliberately excluded from the plan hash. A new success, failure, cooldown, checkpoint, revision, or local dataset metadata change invalidates the plan. Arbitrary direct edits to rows outside the normal workspace are not detected by a full content hash; the retained log metadata is a state pin, not a payload integrity proof.

Apply uses the existing workspace lock and transaction; it refuses any unreconciled local run, including expired leases, and rechecks every credential binding. Only operational configuration and revision are changed. All current retained data, identity keys, checkpoint, enabled state, prior receipts, principal/schema pins and retry cooldown remain intact. SAP's redundant day fields are updated together with the common runtime fields. The receipt is written in the same transaction. Replay of a matching committed request returns its historical receipt, not another mutation; the latest 100 receipts are retained. Receipts contain local profile identifiers, revisions and operational settings, not source payloads.

The local scheduler's existing revision check prevents outdated authorizations from following a changed profile. Gateway configuration digests change too; a separately coordinated Gateway worker and approved job rebind are still necessary. Already uploaded snapshots and downloaded artifacts are not revoked by tuning. This module does not remotely cancel in-flight calls, erase a dataset, change source credentials, or automatically repair historical completeness. It does not replace the historical reconciliation or credential rotation lanes.

## Local API

All endpoints require the existing human dashboard session.

- GET /v1/enterprise-connectors/tuning: operational fields, profiles and bounded receipt inventory.
- POST /v1/enterprise-connectors/tuning/preview: exact targets, each with profile_id, revision and at least one changed supported numeric field.
- POST /v1/enterprise-connectors/tuning/apply: same target selection, plan_sha256 and three explicit boolean confirmations: confirm_exact_changes, confirm_dispatch_reauthorization and confirm_workers_reconciled.

Example preview:

    {"targets":[{"profile_id":"rest_0123456789abcdef01234567","revision":1,"window_hours":12,"overlap_minutes":30}]}

No provider requests occur in these endpoints. The API does not accept a replacement source definition or arbitrary configuration patch.

## Verification

Regression tests use the actual retained workspace and mocked source transport to cover atomic multi-profile updates, rollback, preserved checkpoints and event identity, replay, stale state, active and expired leases, consent, provider-specific ranges, source-scope refusal, and old local schedule fencing. Focused enterprise stack CI includes the test file. Live provider recovery and actual distributed worker coordination remain distinct acceptance tasks.
