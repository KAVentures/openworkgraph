# Customer-run automation release inventory (R18)

A deployment in **Automation monitoring** already retains imported run reports,
a claimed workflow specification hash, an optionally attached reviewed design,
signed report-channel diagnostics and incident triage. These things do not
independently tell OWG whether an executable version was deployed, promoted,
paused or retired.

**Release inventory** adds an operator-reviewed, local-only append-only history
to the existing deployment rather than another event store or remote control.

## User workflow

1. Import a run report, optionally attach the reviewed
   `workflow.spec.json`, and open **Release inventory** on that deployment.
2. Choose an operator-declared stage: planned, shadow, pilot, production,
   paused or retired.
3. Enter a short release label, optional artifact SHA-256 digest and a short
   note without secrets. Pilot and production require an artifact SHA-256.
4. Explicitly confirm the declaration is **not** a remote deployment command.
5. Save. The review pins the current reporting spec hash and attached design
   hash, and records UTC time with immutable revision numbers. Read the
   complete version history and locally retained report-spec claim counts.

Pilot and production require a reported workflow spec, an attached reviewed
design matching that spec, an entered artifact digest and at least one
retained run claiming that spec. **These are necessary review checks, not
sufficient evidence of a deployed version or successful business outcome.**
When the monitoring store has no spec, only the non-production declarations
are allowed.

A retired deployment cannot be reactivated by changing a label. To record
another installation after retirement, import it as a new deployment.

## API

Existing local monitoring dashboard sessions only:

- `GET /v1/automation-monitoring/deployments/{deployment_id}/release`:
  current review (or null), immutable history, pinned hashes, retained run
  counts and explicit unknown-verification flags.
- `POST /v1/automation-monitoring/deployments/{deployment_id}/release`:
  a strict bounded JSON review. Supply `expected_revision`,
  `expected_reported_spec_sha256`, `expected_design_sha256`,
  `stage`, `release_label`, `artifact_sha256`, `reviewer_note` and
  `user_confirmed=true`.

Missing explicit confirmation is 403, stale revision/spec/design is 409,
invalid stages/digests/release labels are 422. JSON duplicate keys and
nonfinite constants are rejected, and requests over 8 KiB are refused.

At most 30 revisions are retained. Capacity exhaustion refuses further
writes rather than silently discarding audit history. Deleting a monitored
deployment atomically deletes its release reviews alongside its runs and
steps. Nothing is shared or uploaded by this feature.

## Interpretation and limits

All lifecycle stages are **operator assertions**. A production declaration
does not start a worker; a paused declaration does not stop one; retirement
does not revoke a remote token or rotate a signed channel. An artifact hash is
user-entered, not measured from the actual running binary. Imported
run-report spec hashes are claims by the automation.

The API always states:

- `remote_deployment_state_verified=false`
- `executable_artifact_verified=false`
- `business_outcome_verified=false`
- `remote_business_actions_triggered=false`
- `rollback_or_remote_pause_performed=false`

Run monitoring, independent source-state reconciliation and authenticated
provider receipts remain distinct boundaries. No automatic business write,
case replay, manual review bypass, remote source read, new capture permission
or expanded organization sharing is introduced.

## Tests

`tests/test_automation_release_inventory.py` tests versioned history,
production preconditions, exact-spec/design change detection, stale edits,
bad types, terminal retirement, capacity, atomic deletion and authenticated
HTTP validation. The existing monitoring browser acceptance additionally
creates an actual pilot review, checks the artifact requirement and confirms
that history remains visible.
