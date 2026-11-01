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
2. The two member observations have **different exact capture scopes**.
   This yields `capture_scope_change` (not proof that a person stopped work).

Both reasons may be represented by a list of reason codes; the first episode
has an empty `boundary_before`. The source record references, tab/clipboard
tokens, actor IDs, device IDs, tenant IDs, window titles and foreign-record
details are **never added** to the episode projection. Only already-present
member event IDs, UTC observation times, counts and reason codes are returned.

A long clock gap by itself does **not** establish an interruption, just as it
does not establish a join. Missing observations, unknown/unreferenced surfaces
and a foreign tenant/actor/device/session are not evidence of an interruption.
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

Synthetic tests exercise two interleaved cases, unknown observations, different
tenants/actors/devices, cross-session same-record resumption, clock-only gaps,
exact counts/truncation, privacy and existing workflow-evidence integration.
Cross-platform native pytest, browser/accessibility, scale, packaging and
release checks must pass on the exact PR head before merge. These synthetic
checks do not constitute real consenting-user validation.

This improves reviewable **segmentation of observed evidence**, not assignment
F1 by itself. The preregistered held-out F1 >= 0.90 and contamination <= 0.05
targets are **not established** by this change. No private evaluation truth,
model/API spend, source-provider calls or business writes are introduced.
