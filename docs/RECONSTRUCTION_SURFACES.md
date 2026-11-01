# Observed surface runs for linked work (read-only)

Roadmap: [#264 Stage 2](https://github.com/KAVentures/openworkgraph/issues/264)
and [#326 Section A](https://github.com/KAVentures/openworkgraph/issues/326).

Implementation: `server/reconstruction_surfaces.py`, integrated into the
existing `linked_work_items` object returned by the workflow-evidence API.
This is a functional addition to the existing activity segments and observed
case-switch timeline, not a new endpoint, background capture process, or
provider connector.

## Analyst capability

Within an already evidence-linked work item, OWG now identifies **observed
surface runs**: consecutive admitted member observations using the same
normalized app identity in the same verified capture scope. An observed
Browser → Terminal change is an app-identity boundary; an unknown app remains
**unknown**, never a synthetic "Other app". A change or absence of exact
organization/actor/device/session identity breaks surface continuity, not
an inferred app switch. A long time gap never creates a boundary or a link.

For example, A has two Browser observations, then a Terminal observation,
then an unknown-surface observation, then two Browser observations. The
existing workflow-evidence response includes four runs with event-ID and
observed-time provenance, one observed app change and one unknown observation.
The Browser runs reuse the same response-local `surface_01` pseudonym, but
this does not prove continuous work between them.

Raw app names, window titles, URLs, tenant, actor, device and session IDs are
**not returned** by the new surface projection. Surface IDs are numbered
locally per returned item and are not stable across queries, users or time.
No new canonical work-text retention or cloud sharing is introduced.

## Additive fields on each linked item

```json
{
  "observed_surface_run_count": 4,
  "observed_app_identity_change_count": 1,
  "observed_unknown_surface_observation_count": 1,
  "observed_surface_scope_boundary_count": 0,
  "observed_surface_runs_truncated": false,
  "observed_surface_runs": [
    {
      "run_index": 1,
      "boundary_before": [],
      "surface_id": "surface_01",
      "surface_known": true,
      "capture_scope_verified": true,
      "first_event_id": "e0001",
      "last_event_id": "e0002",
      "first_observed_at": "2026-10-20T00:01:00+00:00",
      "last_observed_at": "2026-10-20T00:02:00+00:00",
      "observed_event_count": 2,
      "event_ids": ["e0001", "e0002"],
      "event_ids_truncated": false
    }
  ]
}
```

Up to 24 runs and 20 IDs per run are shown, with exact counts over all
validated **members of the returned linked item** even when display is
truncated. The example abbreviates the runs. Boundaries can be
`app_identity_change`, `unknown_surface_boundary`,
`capture_scope_change_or_unverified`, or an initial
`unknown_surface` marker. Only a change between two independently
known apps in the **same fully verified capture scope** contributes to
`observed_app_identity_change_count`. Unknown observations are never
silently merged into a known run, even if consecutive unknown observations
might happen to refer to the same app.

## Privacy and correctness limits

The existing source-linker decides all memberships before this projection
runs. It does not merge cross-record cases, infer task identity, change
confidence, authorize actions, infer human handoffs or measure employee
productivity. Distinct observed app names are labels, not verified app
identity or proof of a business workflow. A case spanning actors/sessions
can still be one source-linked record while having separate surface runs.

The projection preserves existing `linked_work_items` v1 and REST/MCP
read-only access/consent boundaries. There are no provider/API calls,
persistent writes, model costs or changes to private evaluation truth.

## Acceptance

Run from repository root:

```bash
python -m pytest -q tests/test_reconstruction_surfaces.py tests/test_reconstruction_switches.py tests/test_reconstruction_segments.py tests/test_work_item_links.py
```

Synthetic tests cover same-app normalization, app changes, unknown surfaces,
cross-actor and missing-session boundaries, private app/title non-disclosure,
exact counts with truncation, no time-only joins and existing workflow-evidence
API integration. Required native Ubuntu/macOS/Windows pytest, browser,
accessibility, scale, process collaboration, starter and release/packaging
checks must pass on the **exact PR head** before merging.

Real consenting-user acceptance and preregistered held-out assignment scoring
are outstanding. F1 ≥ 0.90 and contamination ≤ 0.05 are not established.
