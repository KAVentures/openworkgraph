# Record participation × app-surface coverage (Worker C, #326 A / #264 Stage 2)

The existing **authorized, read-only** `linked_work_items` projection inside
workflow evidence now includes `observed_record_coverage` for already-linked
work items. This is an analyst-facing **observed presence map**, not a
source-authoritative case, an employee activity score, a handoff, a verified
business outcome or evidence that the work was completed.

## What the analyst can inspect

For each independently admitted exact source-record identity, OWG lists the
response-local pseudonymous participants and app surfaces on which they were
observed. Each participant × surface pair includes its first and last admitted
event ID and UTC observation timestamp. Two participants working in two apps
on overlapping records appear as separate record rows; interleaving does not
make the cases one task. Repeated observations on the same pair are **not**
interpreted as continuous work, duration, effort or productivity.

The additive per-item keys are:

- `observed_record_coverage_count`, `observed_record_coverage` (at most 12
  records), `observed_record_coverage_truncated`;
- `observed_multi_participant_record_count`;
- `incomplete_scope_record_observations_excluded`,
  `unknown_surface_record_observations_excluded`, and
  `cross_tenant_record_coverage_suppressed`;
- per-record `record_id`, `distinct_observed_participants`,
  `distinct_observed_surfaces`, `participant_surface_pair_count`,
  `participant_surface_witnesses` (at most 24), and explicit truncation;
- each witness has `participant_id`, `surface_id`, first/last event IDs and
  UTC timestamps, and `continuous_activity_inferred: false`.

Exact counts describe **admitted observations only**. They do not claim full
capture coverage. The response never adds raw organization, actor, device,
session, app or business record identity through this feature. Pseudonyms are
local to one work-item projection and must not be persisted as identities.
Existing opaque resource refs in the older work-item contract remain
unchanged.

## Conservative admission and privacy

Only already-linked, untainted observations with a literal exact source
record, complete organization/actor/device/session capture scope and a known
app are eligible. Incomplete scope and unknown/generic surfaces are counted
as exclusions, not silently mapped to another participant or `Other app`.
Conflicting complete tenant scopes for one record suppress that record's
coverage entirely. A record without any eligible observation is not listed.
No titles, URLs, clipboard text, raw app labels, identity tokens or event
payloads are inspected by this module.

The linker and membership edges are unchanged. There is no time-only join,
no implicit cross-actor transfer, no new connector, grant, upload, business
write, background sync or persistence. Removing/revoking observations and
recomputing evidence removes their participation; this module holds no cache.

## Acceptance and limits

`tests/test_reconstruction_case_coverage.py` covers two actors/two apps and
two interleaved records, first/last witness provenance, private-label
non-disclosure, incomplete scope, unknown app, cross-tenant conflicts,
non-linking of adjacent unrelated records, recomputation after source
removal, exact counts/truncation and existing workflow-evidence integration.

These are **synthetic** tests, not real provider/customer acceptance. Native
Ubuntu/macOS/Windows pytest, browser/accessibility, process collaboration,
scale, starter and packaging/release checks must pass on the exact PR head
before merge. This feature does not establish the preregistered assignment
F1 ≥ 0.90 or contamination ≤ 0.05; the frozen held-out evaluation must be
run and reported separately without editing private truth or score floors.
