# Reviewed historical connector reconciliation

This workflow recovers late-arriving evidence within a previously traversed
connector change-time window without rewinding the live checkpoint. It is
a bounded, separately approved local read, not a continuous backfill job,
a provider completeness guarantee or a history reconstruction mechanism.

## Operator workflow

1. Stop or reconcile external Gateway workers and pause/remove competing
   recurring schedules. The local ingestion lease prevents simultaneous
   retention into the same profile, but remote workers cannot be stopped
   by this local action. The operator must coordinate them.
2. Open the enabled profile in Enterprise connector operations. An
   existing completed checkpoint and current credential binding are
   required. Open **Historical window reconciliation**.
3. Specify an exact timezone-qualified half-open change-time interval.
   The interval must be within the originally approved initial timestamp
   and current checkpoint, no longer than the configured window, and
   within the local 365-day cap and provider-specific cutoff. SAP requires
   complete UTC-encoded business dates; whole-second providers require
   exact second boundaries. Google retains its narrower history limit.
   No automatic overlap is applied.
4. Preview the plan. This does not read the provider. The plan digest
   includes the profile revision, exact configuration digest, retained
   log identity, live checkpoint, interval, and latest historical receipt
   identity. Changed pins require a new preview.
5. Approve the historical read, retention, and worker reconciliation
   separately. Execute. The original connector scope, credential binding,
   event identity key, provider verification and bounded read budgets
   remain in force.

A late event is appended using its original source identity. An exact
repeat deduplicates. A conflicting retained payload fails closed rather
than overwriting evidence. All projected events and the historical
success receipt commit in the same existing local retention transaction.
A previously empty routine window can acquire its first retained log
through a successful historical read. The live checkpoint, routine
success/failure receipt and routine timestamps do not change. Source
schema and principal verification still apply. A provider rate limit
retains its normal cooldown.

## Receipts and recovery

The profile holds at most 30 recent historical receipts. While a request
ID's receipt is retained, an exact repeat returns that receipt without
another source read; reuse with a different plan or interval is refused.
An explicitly new reviewed request may reread the same interval. The
receipt reports counts and outcome, not raw records, source secrets,
provider responses or verified complete history.

An active historical intent uses the existing profile lease. Routine
reads cannot take its place, even after expiry. Stop workers, inspect the
retained log, and explicitly reconcile an expired intent as uncertain
before permitting another run. This does not contact the provider.
Changing the profile revision invalidates the historical intent and records
an uncertain receipt; deletion of the retained dataset clears the
historical state and prevents automatic recreation. An interrupted or
failed read is not proof of no provider activity. A successful local
recovery may invalidate the canonical content of a previously prepared
snapshot; prepare and approve a new exact publication rather than silently
replacing the old one.

## API

Dashboard-session-only POST endpoints under
/v1/enterprise-connectors/rest/{profile_id}:

- historical-preview: revision, since, until.
- historical-execute: the same window, request_id, expected_plan_sha256,
  confirm_historical_read, confirm_retention, confirm_workers_reconciled.
- historical-reconcile: revision, request_id, confirm_workers_stopped,
  confirm_uncertain_outcome.

Preview is passive with respect to the provider, although existing local
workspace access may initialize its schema. Historical execution shares
the fixed adapter dispatch and is reviewed for the nine retained providers
in integration candidate #1589. Other adapters must be reviewed before
being included. Recurring and Gateway dispatch do not automatically
execute historical windows. Inventory exports include counts and review
flags, not raw historical window receipts or source credential references.

## Validation

Dedicated regression fixtures cover late evidence, empty-window recovery,
exact retry receipts, stale review, conflicting identities, atomic
rollback, expired leases, profile revision invalidation, and preserved
checkpoint and routine metadata. The browser fixture recovers a late row
from a real local SQLite source. Focused draft acceptance runs the new
history tests, existing REST/SQL regression modules, compilation, and
Chromium configuration/recovery acceptance.

Full repository execution, native provider tenant acceptance and the
parent integration candidate's CI are outstanding. Local direct git
checkout was blocked by DNS during authoring. This change does not
independently verify provider history completeness, automatically repair
missing source records, or support arbitrary historical queries.
