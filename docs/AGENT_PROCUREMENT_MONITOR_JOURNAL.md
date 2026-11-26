# Durable local monitoring journal

An opt-in local journal for the finite R26 monitoring plan. It uses the
existing local-owner SQLite database and authenticated dashboard session,
not an external telemetry source. The offline review operations remain
nonpersistent.

## Operations

All endpoints are POST /v1/process-catalog/procurement/{operation} with
workflow_id and revision query parameters and the existing JSON-text
document envelope. The dashboard exposes the same operations.

- router-journal-list: document {} lists retained journals in this revision.
- router-journal-append: document {initial, monitor_plan, snapshot, request}.
  request has expected_head_sha256 (null for first checkpoint, otherwise
  the exact returned head) and confirmed=true. The complete upstream chain
  and monitor plan are recomputed. The previous accepted snapshot is read
  from local storage, not supplied by the caller. A monitor ID in the
  same process revision cannot silently bind a different plan.
- router-journal-detail: {monitor_id} returns the latest report, sticky
  incidents, head and bounded chained audit history.
- router-journal-decide: {monitor_id, expected_head_sha256, disposition,
  reviewer_alias, confirmed:true}. Dispositions: refer_for_continued_monitoring_review,
  refer_for_rollback_review, hold. The reviewer alias must differ from the
  frozen coordinator alias; it is not independently authenticated.
  Positive continuation requires the latest report to pass and no retained
  incident or rollback referral. Rollback and hold remain review actions.
- router-journal-forget: {monitor_id, expected_head_sha256, confirmed:true}.
  Removes that local journal and all its audit entries transactionally.
  External exports, SQLite backups and physical erasure are not verified.

Append and decision require the latest saved process revision, checked in
the same SQLite write transaction. Historical list/detail/removal remain
available for exact retained revisions. Concurrent updates are serialized
and a stale expected head is rejected (409). A failed write rolls back;
no provider or business action is performed.

The database retains the latest normalized snapshot and report, not raw
case inputs or upstream trial bundles. Audit entries retain hashes, times,
decision aliases and compact incident/coverage summaries, not full old
snapshots. Hash chaining detects inconsistent local records; it does not
protect against an administrator who can rewrite the entire database.
The latest report may pass after a transient regression; sticky incident
flags still block continuation across all retained checkpoints. A rollback
referral remains sticky as well. There is deliberately no incident-clearing
operation in this version. Use separate review of a new plan rather than
silently resetting the same journal.

Limits: 20 journals per local database, 128 audit entries per journal,
8 MiB state per journal. Capacity exhaustion does not silently prune
history. Retain/export evidence before explicitly removing a journal.
No live polling, authenticated source telemetry, actual traffic assignment,
production execution, causal benefit, or rollback authority is established.

The optional [scoped producer interface](AGENT_PROCUREMENT_MONITOR_PRODUCER.md) adds credential-authenticated observation batches to the retained journal. New checkpoints can retain the verified monitor plan and initial source-reference hashes (no raw trial inputs), enabling incremental evaluation without resupplying the upstream bundle. Grant secrets are never retained in journal history or report exports.

The [local workforce overview](AGENT_PROCUREMENT_WORKFORCE_OVERVIEW.md) aggregates retained journals across saved workflows without combining currencies or counting exact overlapping cohorts. It also exposes a prioritized incident and deadline queue.

For closed-window declarations of booked revenue, expenses and telemetry reconciliation, see [finance reconciliation](AGENT_PROCUREMENT_FINANCE.md). Booked figures are not added to modeled savings or imputed labor.

Optional unattended deadline review uses the existing local scheduler and [journal-specific consent](AGENT_PROCUREMENT_MONITOR_SCHEDULE.md). Idle ticks do not consume journal entries.

Retained-only journal list, detail, consent withdrawal, producer revocation and
removal remain available by exact workflow/revision scope even after the
reviewed process has been deleted. In that case the API reports a null current
revision; the local workforce overview identifies the orphaned journal.
