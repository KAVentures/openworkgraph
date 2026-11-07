# R18 — Run incident reconciliation against retained evidence

OWG's run monitoring stores reports in the local `automation_runs.duckdb`
database under unique (deployment, run ID) pairs. Reimporting a given run ID
updates that retained row. Before this change, incident triage incremented the
failure/handoff **occurrence count for each submitted report**, including
multiple accepted signed reports with the same run IDs or a manually reimported
file. As a result, the dashboard could overcount reported failures and
reopen a previously resolved incident even though no new run was present.

## Behavior

After a successful manual report import or an authenticated signed channel
ingestion, OWG now reconciles the incident groups from the **latest retained
run database**, not the transport envelope:

- A failed run belongs to `run_failures`; a handed-off run belongs to
  `run_handoffs`. Each is grouped by the **first** step with the matching
  reported status, or `no_step_reported` when none exists.
- A run ID counts exactly **once** in the currently matching incident for its
  deployment. An exact resend, a new signed sequence containing previously
  imported run IDs, and a manual reimport do **not** increase the count.
- If a run's retained status changes to succeeded/running/cancelled, the
  previous failure or handoff group now shows **zero currently matching runs**.
  A group is retained for its human notes and review history; it is **not**
  automatically marked resolved.
- If a failed run changes its failing step, the old group decreases and the
  new group increases. Distinct run IDs are compared via a stable SHA-256
  digest, so a different failure can trigger review even when the total count
  remains unchanged.
- A **resolved** incident reopens only when its retained run membership
  changes at a constant or increasing positive count. A pure decrease is not
  treated as a new failure. The existing review notes, first-seen time, and
  prior review history remain in place. An exact replay does not alter
  `last_seen` or the incident status.
- The dashboard labels run-incident counts as **distinct retained runs**.
  Counts for failed signatures, replay conflicts, lost sequences, and version
  drift remain **report-channel events**, not distinct runs.
- For legacy run incidents without membership digests, a first reconciliation
  corrects inflated `occurrences` values to current counts without
  misrepresenting the migration as a new failure or reopening a resolved item.
- Deleting the deployment deletes the corresponding run incidents, digests,
  sample references and notes using the pre-existing erasure path.

The existing `GET /v1/automation-monitoring/incidents` returns the extra
fields `current_run_count`, `count_basis:
"distinct_retained_run_ids"`, and `run_membership_sha256` for reconciled
run groups. `occurrences` is the **current count of distinct retained run
IDs** for these groups; for channel/security incidents, it retains its
existing transport-event meaning. At most 10 run IDs per group are returned
as examples, sorted by run ID. The digest and complete membership remain
local; no additional provider permission or export is added.

## Implementation and safety

`server/automation_incidents.py` holds the existing local run database lock
until it has compared the retained data with the local incident sidecar,
preventing concurrent imports from racing the reconciliation. Its DuckDB
query reads only reported failed/handoff run IDs and the first matching
reported activity; results are streamed in small batches, while incident
identity/group counters remain bounded by the existing incident limit.
Updates to the JSON incident store are atomic on the local filesystem.

Incidents remain **claims supplied by the external automation**, not verified
business outcomes. Reconciliation does not execute agents, retry tasks, replay
source writes, approve policy, grant permissions or contact third parties.

Crash/recovery limitation: a run import may commit before a local incident
sidecar update. The next successful import for that deployment reconciles
the full retained history; a standalone rebuild can also be invoked with
`incidents.reconcile_deployment(deployment_id)` from trusted local code.
The endpoint does not expose a new unauthenticated rebuild operation.

## Concurrent human triage integrity

Incidents now carry a monotonic `review_revision` integer (initially 0).
Every `POST /v1/automation-monitoring/incidents/{incident_id}/triage`
must include **both** `expected_review_revision` and
`expected_last_seen` from the latest authenticated incident listing.
The former protects other human reviews; the latter protects against
new or changed report observations. The OWG dashboard sends both values.

A second reviewer who acts against an older revision receives HTTP **409**,
rather than overwriting another human's acknowledgement, resolution, or note.
A completed status change or newly recorded note increments the revision;
a no-op retry with the *current* revision does not. Earlier incident records
without the field are treated as revision 0 and migrate on their first review.

This is a deliberate safety change to the human-review API: clients
that omit either expected snapshot field must refresh the incident and
include both; otherwise the request is refused. No report ingest, signed channel or
case action needs this field. The pre-existing `expected_last_seen` continues
to prevent reviewing an incident whose reported occurrences changed since
it was loaded.

## Bounded signed sequence gaps

The same R18 review also closes an operational denial-of-service risk in signed
report ingestion. Previously, receiving a valid HMAC-signed report with a large
forward sequence number constructed `list(range(previous + 1, incoming))` in
memory. A misconfigured producer could require immense allocation.

The channel now counts the gap arithmetically and keeps **at most 1,000 recent
missing sequence numbers** for recovery. When more gaps exist than fit, the
existing `missing_truncated` flag is set and remains set even if some retained
IDs subsequently arrive. `new_missing_sequences` is an **inferred difference
in signed sequence numbers**, never a verified count of lost deliveries. Signed
sequence numbers outside **1 to 2⁵³−1** are refused, ensuring they are exactly
representable by browser and JSON clients.

This does not change signing keys, revoke/rotate behavior, existing replay
windows, or access permissions. Because older gaps can be truncated, operators
must not equate a zero retained missing-list count with proof of complete
delivery if `missing_truncated` is true.

Tests in `tests/test_automation_report_channels.py` cover large, valid signed
jumps, the 1,000-entry retained cap, late recovery, persistent truncation, and
the exact-integer boundary.

## Test coverage

`tests/test_automation_incident_reconciliation.py` exercises repeat signed
sequences, exact signed resends, manual reimports, status correction,
failure-step changes, equal-count/different-membership replacements,
more than ten sample IDs, corrected legacy inflated counters, concurrent
human triage semantics, and erasure. The existing
`tests/browser/automation_report_channels.cjs` tests the visible count
semantics, exact replay not reopening a resolved incident, then a genuinely
new failed run reopening it, plus the existing WCAG and mobile checks.
All native CI and browser checks must pass before merge.
