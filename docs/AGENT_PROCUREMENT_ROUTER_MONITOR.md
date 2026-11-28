# Multi-window router monitoring (R26)

This is a finite, offline review of declared monitoring evidence after a
positive initial pilot expansion referral. No traffic, provider, scheduler,
live telemetry source or deployment is changed.

## Freeze

The initial object contains upstream, pilot_plan, captures, request (exact
pilot decision request) and receipt. The entire chain is recomputed; a
positive initial expansion referral is required. The freeze bundle has
initial and request. The monitoring request contains monitor_id,
coordinator_alias, external_monitoring_approval_sha256 (a declaration),
maximum_reporting_delay_ms (0–30 days), confirmed=true, and windows.
Each window contains window_id, start_ms, end_exclusive_ms,
allocation_seed_sha256 and cases (same opaque token/category contract as
the pilot). Windows must be ordered, contiguous, nonoverlapping and after
the initial pilot snapshot. Maximum 12 windows and 1,500 cases total, up
to 500 per window. Case tokens cannot be reused across windows, the initial
pilot or exact prior evaluation hashes. The original pilot policy, versions,
portfolio, accounting rates, rollback target and coverage requirements
are retained. Allocation is deterministic and offline.

## Snapshot review

Bundle: initial, monitor_plan, snapshot, previous_checkpoint. Snapshot
contains format openworkgraph.procurement-router-monitor-snapshot.v1,
monitor_plan_sha256, as_of_ms, windows (list of window_id and observations).
Observations follow the exact pilot observation schema, with the respective
child window slot IDs. Each source reference, case and slot may appear
only once across the snapshot. Initial pilot source hashes cannot be reused.

For the first snapshot set previous_checkpoint=null. For subsequent
snapshots supply previous_checkpoint containing the exact previous snapshot
and expected_snapshot_sha256 from the previous review. The previous
snapshot is independently recomputed against the plan, and its canonical
pin (windows sorted by ID, observations by slot) verified. Time must
strictly advance and previously recorded observations cannot disappear or
change. New late observations can fill previously missing slots if their
event times fall in the original window and are no later than the cutoff.
Existing observations are immutable; corrections require separate external
reconciliation and a new explicitly reviewed evidence lineage. No silent
amendment is supported.

Future windows are excluded from cumulative rates. Every started window
includes all allocated cases, even unrecorded ones. Unknown required
evidence after the frozen reporting grace period is overdue. The previous
checkpoint's overdue windows are separately reported if a checkpoint is
supplied, even if they later resolve. Known adverse signals and established
category failures in any window trigger rollback review; pooled improvement
cannot override an individual window failure. Cumulative comparisons
recompute from all started case rows, not averages of window averages.
The final continuation referral requires every scheduled window closed and
passing, all cumulative category checks passing and no rollback triggers.

## Decision and interfaces

Add request with disposition (refer_for_continued_monitoring_review,
refer_for_rollback_review, hold), reviewer_alias distinct from coordinator,
expected_monitor_plan_sha256, expected_report_sha256, confirmed=true.
Continuation is refused unless the required checks pass. Rollback review
and hold may be referred by a reviewer even without an automated trigger.

Dashboard and authenticated no-store endpoints support router-monitor-freeze,
router-monitor-review and router-monitor-decide. Freeze/decision require
latest saved revision; historical reviews are allowed. The local CLI writes
exclusive private output:

```sh
python -m shared.agent_procurement_router_monitor router-monitor-review --process-export process.json --bundle snapshot.json --out-dir reviewed
```

Retain the snapshot input and exported snapshot hash separately. Prior-pin
validation is only against the checkpoint supplied by the caller, not an
authoritative persisted append-only store. A caller could omit the previous
checkpoint; that absence is explicit in the report. Source evidence, external
approval and chronology are not independently authenticated. These are
descriptive disjoint-arm comparisons, not causal effects, verified ROI or
authorization to operate production.

Each closed-window cumulative prefix is recomputed separately so a later
passing pooled average cannot mask an established earlier prefix failure.
Any rollback trigger in the supplied prior checkpoint blocks continuation
at the next review even if current pooled checks recover. This is not a
persistent incident history: retain all checkpoints and refer for separate
review rather than discarding an earlier trigger.

For local persistence with automatic prior-checkpoint lookup, chained audit entries and sticky incident flags, use [the monitoring journal](AGENT_PROCUREMENT_MONITOR_JOURNAL.md). Offline snapshot review remains nonpersistent.
