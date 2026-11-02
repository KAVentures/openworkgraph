# Observed same-record participant continuity (#326 A / #264 Stage 2 / #370 R2)

The existing, authenticated **read-only workflow-evidence response** includes a
bounded actor-participation timeline inside each already linked work item.
No separate endpoint, connector, source read, new permission, employee identity
store or background synchronization is introduced.

## What an analyst can inspect

For an exact source record observed as **A → B → A**, the corresponding linked
item exposes:

- `observed_same_record_participant_count`: distinct complete, literal
  tenant/actor identities observed in that item (not an employee roster).
- `observed_same_record_actor_change_count` and
  `observed_same_record_actor_return_count`: exact totals, even when the
  bounded timeline is truncated.
- `observed_same_record_actor_changes`: at most 24 consecutive observed
  changes *within one exact source record*, each with response-local
  `record_01` / `participant_01` labels, source event IDs, UTC timestamps,
  return indicator, and `intervening_same_tenant_observation_count` for
  validated observations between the endpoints in the same tenant. `continuity_unverified: true` and `handoff_verified: false`
  are always explicit.
- `same_time_cross_actor_order_suppressed` and
  `incomplete_actor_scope_observations_suppressed`: boundaries withheld
  because order or actor identity cannot be established.
- `observed_same_record_actor_changes_truncated`: the list is a sample of
  the *first* 24 chronological changes, not an assertion of complete coverage.

These are observed access/participation changes, **not source-confirmed
handoffs**, work assignments, collaboration proof, elapsed work time,
productivity metrics, task starts/ends, or business outcomes. An actor return
means only that the same scoped actor was observed on the same record earlier
in this response. Actor labels are local to each work item, not stable IDs.

## Safety and reconstruction rules

Only observations already admitted by `link_observations` with a valid,
unconflicted exact `resource_reference` are eligible. The underlying linker
validates source/provider/kind/host/organization identity, rejects duplicate-ID
conflicts and never uses time proximity to create membership.

A participant endpoint additionally needs **all four explicitly present,
nonempty, valid** `organization_id`, `actor_id`, `device_id`, `session_id`
values. Legacy missing fields are not wildcards. Missing or malformed capture
identity breaks a record's participation sequence. Equal-timestamp actor
changes cannot establish a before/after ordering and are withheld. Different
source records, including records already linked by clipboard transfer, never
become a participant-change edge. An intervening observation from another
record does not create an edge, and is counted as uncertainty only when it
belongs to the same tenant.

Only event IDs, UTC timestamps, counts and response-local pseudonyms are
returned. Raw actor, tenant, device, session, host, tab, clipboard, URL, title
and captured text values are not emitted. This feature consumes only the
already authorized bounded local evidence window and does not expand consent
or sharing. Revoked/retained-away evidence is not reconstructed from history.

## Acceptance and limits

Run `python -m pytest -q tests/test_reconstruction_actor_continuity.py
tests/test_work_item_links.py tests/test_reconstruction_owg_native.py` in
native CI. Synthetic acceptance covers A→B→A, interleaved foreign records,
same-item cross-record clipboard bridges, incomplete scope, equal timestamp,
tenant/source conflict, legacy scope, bounded output, privacy and the actual
workflow-evidence API. Required Ubuntu/macOS/Windows pytest, browser,
accessibility, scale, starter and packaging/release workflows still gate merge.

This is a deterministic *explanatory projection* of already admitted evidence,
not a new assignment classifier. It does not establish family-holdout F1
≥ 0.90, contamination ≤ 0.05, real cross-employee consent/pilot quality, or
independently authorized source-confirmed handoffs. No private truth or
preregistered threshold is changed.
