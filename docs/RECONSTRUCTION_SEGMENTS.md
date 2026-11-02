# Observed activity episodes in linked work items

Roadmap: [#326 Section A](https://github.com/KAVentures/openworkgraph/issues/326),
[#264 Stage 2](https://github.com/KAVentures/openworkgraph/issues/264).
Implementation: `server/reconstruction_segments.py`, integrated into
`server/work_item_links.py`'s existing `linked_work_items` projection.

## Analyst-facing behavior

The existing local, read-only workflow-evidence response now includes bounded
`activity_segments` within each already-linked work item. These are **observed
episodes**, not asserted tasks or completed cases. An episode ends only when:

1. A **different literal source record** is observed between consecutive
   member events in the **same exact organization, actor, device and capture
   session**. This yields `interleaved_other_record`.
2. A **known different application** is independently observed without a source
   record, strictly between two member observations in the **same known app**,
   with a known capture source and the same **complete** organization, actor,
   device and session. This yields `observed_other_app_without_record` — an
   **uncertain observed interruption**, not proof of another business case.
3. An **independently observed, different browser tab without a source record**
   appears strictly between two member observations in the **same exact tab,
   known app and capture source**, with the same complete organization, actor,
   device and session. This yields `observed_other_tab_without_record` —
   an **uncertain focus interruption**, not a different business case.
4. Two **consecutive member observations** name different independently
   observed exact source records in the **same complete capture scope**, and
   their UTC timestamps are strictly ordered. This yields
   `observed_member_record_change`: a *record-context* episode boundary, not
   a new membership, verified task switch, handoff or outcome. This is useful
   when an accepted clipboard bridge already links two records into one
   work item: A → B → A remains one linked item but has three observed
   record-context episodes. A missing/unreferenced member, tied timestamp,
   incomplete scope or cross-scope observation cannot supply this boundary.
   Existing independently observed foreign-record interruptions take precedence.
5. The two member observations have **different exact capture scopes**.
   This yields `capture_scope_change` (not proof that a person stopped work).

Both reasons may be represented by a list of reason codes; the first episode
has an empty `boundary_before`. The source record references, tab/clipboard
tokens, actor IDs, device IDs, tenant IDs, window titles and foreign-record
details are **never added** to the episode projection. Only already-present
member event IDs, UTC observation times, counts and reason codes are returned.

A long clock gap by itself does **not** establish an interruption, just as it
does not establish a join. A known app change without a record can now bound an
observed episode **only** when a trusted, source-observed, unreferenced app event
lies strictly between same-app member anchors. The app event remains unlinked;
no source record, task, verified handoff or business outcome is inferred.
Timestamp ties, missing/incomplete capture scope, other tenants/actors/devices/
sessions, unknown/placeholder or control-bearing app names, missing/unknown
source, contradictory tab/transfer claims, tainted record identities, same-app
loose actions and already-member app activity do not establish this boundary.
A different-tab event cannot establish a boundary if either anchor has an
unknown or conflicting tab, if its source/app differs, if timestamps tie, or
if it carries a clipboard transfer/copy-paste action, malformed action/ephemeral
claims or an invalid resource reference. A same-tab unreferenced event is not
negative focus evidence. The tab token, app, capture identity and other-tab
event ID are not returned; the other-tab observation remains unassigned.
Cross-app boundaries take precedence over cross-tab boundaries, and the
`interleaved_other_record` boundary retains precedence when a distinct
literal source record is actually observed. Unknown observation coverage is
never silently promoted to an uninterrupted business task.
The within-item record-context boundary is **presentation-only**: it never
splits an accepted clipboard-linked work item into separate work items, changes
its `cross_record_join` or `link_basis`, or uses temporal proximity to infer
membership. Removing the B observation recomputes A's episode count. It does
not expose clipboard tokens, capture identity or foreign-record details beyond
the existing work-item reference projection. Direct-linker and authenticated
workflow-evidence response tests exercise the new behavior.

The original source-grounded membership, same-record/clipboard/tab link bases,
uncertainty, ambiguous candidates and item confidence remain unchanged.

## Additive response shape

For each linked item:

```json
{
  "activity_segment_count": 2,
  "activity_segments_truncated": false,
  "activity_segments": [
    {
      "segment_index": 1,
      "boundary_before": [],
      "first_observed_at": "2026-10-20T00:01:00+00:00",
      "last_observed_at": "2026-10-20T00:01:00+00:00",
      "observed_event_count": 1,
      "event_ids": ["e0001"],
      "event_ids_truncated": false
    },
    {
      "segment_index": 2,
      "boundary_before": ["interleaved_other_record"],
      "first_observed_at": "2026-10-20T00:03:00+00:00",
      "last_observed_at": "2026-10-20T00:03:00+00:00",
      "observed_event_count": 1,
      "event_ids": ["e0003"],
      "event_ids_truncated": false
    }
  ]
}
```

The total segment count is exact over the supplied, validated observations;
only the first **16** segments and first **20** event IDs per segment are
included. Existing item and candidate limits remain unchanged. A truncated
response must not be interpreted as full coverage. All fields are derived,
non-authoritative, read-only and do not grant agent/tool authorization.

## Acceptance and limitations

Run from repository root:

```bash
python -m pytest -q tests/test_reconstruction_segments.py tests/test_work_item_links.py
```

Synthetic tests exercise two interleaved cases, unreferenced known-app and independently observed other-tab switches,
strict timestamp ties, same-app anchors, unknown/tainted capture, different
tenants/actors/devices/sessions, cross-session same-record resumption, clock-only
gaps, exact counts/truncation, retention recomputation, privacy and existing
workflow-evidence integration.
Cross-platform native pytest, browser/accessibility, scale, packaging and
release checks must pass on the exact PR head before merge. These synthetic
checks do not constitute real consenting-user validation.

For example, Browser tab A / Case A → Browser tab B / no record → Browser
tab A / Case A produces **two** observed Case A episodes with the uncertain
`observed_other_tab_without_record` boundary, without assigning tab B to A.
Removing the middle observation recomputes one episode. The existing
`linked_work_items` and `workflow_evidence` APIs expose this without new
permissions, new membership edges or a changed response format.

This improves reviewable **segmentation of observed evidence**, not assignment
F1 by itself. The uncertain other-app boundary must not be treated as a
confirmed task switch, handoff, user productivity measure or agent authorization.
No new case memberships or automatic actions are created. The preregistered held-out F1 >= 0.90 and contamination <= 0.05
targets are **not established** by this change. No private evaluation truth,
model/API spend, source-provider calls or business writes are introduced.
