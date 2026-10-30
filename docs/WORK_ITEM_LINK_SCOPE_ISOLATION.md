# Work-item link scope isolation

Roadmap: [#326 Section A](https://github.com/KAVentures/openworkgraph/issues/326),
[#264 Stage 2](https://github.com/KAVentures/openworkgraph/issues/264).

## Defect and boundary

Opaque browser-tab and clipboard-transfer identifiers are **not globally
unique**. Previously, the deterministic linker indexed these IDs across the
entire selected event list. Two unrelated organizations, employees, devices
or capture sessions could reuse an identifier and be incorrectly merged.
The weaker app/surface-continuity candidate could also point across these
boundaries when adjacent observations had the same app label.

These are evidence-integrity and isolation defects, not a justification for
inferring a business case, task completion or an agent permission.

## Conservative rule

`server/work_item_links.py` now groups ephemeral clipboard transfers and
browser tabs only when all four recorded scope dimensions match exactly:

- `organization_id`
- `actor_id`
- `device_id`
- `session_id`

A missing legacy value is represented by an empty component, **not a wildcard**
matching a known component. Malformed or overlong scope values prevent
ephemeral joins for that observation. App/surface-continuity candidates obey
the same scope rule and remain **unmerged**.

The existing exact normalized resource-reference path is separate: a stable
reference can still link observations from different actors or sessions, but
conflicting provider/kind/host/organization identities suppress that reference.
The new rule does not grant cross-tenant access or turn record identity into
task identity. No time-only joins, text, titles, clipboard payloads, URLs,
provider calls or writes are introduced.

## Reproducible synthetic checks

Run from the repository root:

```bash
python -m pytest -q tests/test_work_item_links.py tests/test_reconstruction_owg_native.py
```

The new cases reuse the **same** opaque transfer/tab ID across organizations,
actors, devices and sessions; test same-scope positive linking; require that
adjacent same-app observations cannot create cross-scope candidates; reject
unknown/malformed scope as a wildcard; and preserve cross-actor exact-record
grouping. Published projections do not expose raw scope identifiers.

The preregistered held-out assignment F1 >= 0.90 and contamination <= 0.05
targets are **not claimed achieved**. This is a conservative false-positive
isolation fix, not a model-graded semantic linker or a measured F1 uplift.
Synthetic tests are not real-user, real-provider, or customer-pilot validation.
Any change in linked-item recall should be measured on the existing
preregistered family split, without changing answer-key truth or tuning to
holdout failures.
