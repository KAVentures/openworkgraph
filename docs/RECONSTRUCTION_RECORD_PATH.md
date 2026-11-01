# Observed record path in linked work items (#326 A / #264 Stage 2)

The existing read-only workflow-evidence response now includes an **observed
source-record path** for every returned, evidence-linked work item. This is
a chronological description of admitted observations, not a process model,
causal handoff, independently verified business case or new link signal.

A strict observed clipboard copy from record A followed by a paste to B,
then later observed activity on A, can appear as three record runs:
`record_01 → record_02 → record_01`. The run IDs are **response-local
pseudonyms**; raw record references, tenant/actor/device/session identifiers,
app names, titles, URLs and clipboard contents are not added to the path.
The existing linked item's separate resource-reference field retains its
preexisting behavior.

## Contract and acceptance

Per item, `observed_record_runs` contains at most 24 runs. Each run has a
1-based index, pseudonymous `record_id` or null for unknown, verified-scope
flag, boundary reason, first/last admitted event IDs and UTC instants, and
observed event count. Full-run and observed-record-change counts remain exact
even when the displayed path is truncated.

A direct record change requires **adjacent admitted member observations** naming
two different literal records in the same exact capture scope. A missing or
changed capture scope creates a scope boundary, **not** a cross-actor,
cross-device, cross-session or cross-tenant transition. An unreferenced member
creates an explicit unknown-record run; a later known record does not silently
bridge that uncertainty. Intervening **nonmember observations in the same
exact scope** create an explicit `interleaved_other_observation` boundary
instead of a falsely direct record change, even if the surrounding record
is the same. The global observation positions and scope-local positions are
indexed once per response, then queried with binary search for each member
boundary. Activity by another actor, tenant, device or session cannot
introduce a local interruption. Repeated observations of the same record
remain one run when scope is verified and uninterrupted. A clock gap alone
does not split a run or create a membership edge.

Additive fields:

- `observed_record_run_count`
- `observed_record_change_count`
- `observed_unknown_record_observation_count`
- `observed_record_scope_boundary_count`
- `observed_record_interleaved_boundary_count`
- `observed_record_runs_truncated`
- `observed_record_runs`

The parent `link_contract` states that paths are not verified business
transitions, changes require the same exact capture scope, and unknown runs
cannot create transitions. Existing REST/MCP permissions, source selection,
local-first consent, revocation, retention, API response fields and
Discovery Mode are unchanged. No new data is captured or persisted.

## Verification and limits

`tests/test_reconstruction_record_path.py` exercises an actual multi-record
copy/paste-linked item, return to a prior record, unknown tab-attached evidence,
cross-actor scope boundaries, interleaved foreign-case interruptions, bounded
42-run projection, no raw identities
in the new path, time-only rejection and existing workflow-evidence API
integration. Run with:

```bash
python -m pytest -q tests/test_reconstruction_record_path.py tests/test_work_item_links.py
```

Synthetic tests are **not** a real consenting pilot or preregistered held-out
accuracy result. Native Ubuntu/macOS/Windows pytest and all required exact-head
CI jobs must pass before merging. Assignment F1 >= 0.90 and contamination
<= 0.05 remain unverified. This feature makes already-evidenced record
sequences inspectable; it does not claim that a transition was caused by the
preceding event or that an unobserved business action occurred.
