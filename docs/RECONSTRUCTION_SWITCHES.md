# Observed case switches and returns (read-only task mining)

Roadmap: [#326 Section A](https://github.com/KAVentures/openworkgraph/issues/326)
and [#264 Stage 2](https://github.com/KAVentures/openworkgraph/issues/264).
Implementation: `server/reconstruction_switches.py`, integrated into the existing
`linked_work_items` projection in `server/work_item_links.py`.

## What an analyst can inspect

When a person works on observed record A, switches to observed record B, and
returns to A, the existing local workflow-evidence API can now show the ordered
**observed A → B → A transitions** and mark the return. This is additive to the
per-item activity episodes in `RECONSTRUCTION_SEGMENTS.md`; neither view
changes membership or asserts that an entire task was completed.

Both endpoints of a switch must already belong to **distinct returned linked
work items**, each observation must carry an independently admitted **literal
source-record reference**, and both must be in the same exact organization,
actor, device and capture session. The timeline is computed from the linker's
validated UTC observation order. No timestamp proximity, shared application,
unreviewed candidate, missing scope, or source-only unseen observation can
manufacture a transition. The record identity is used only for evidence
admission and is not repeated in the switch timeline.

A same-scope observation without a qualifying returned item or literal record
anchor is counted as **unattributed** between the two anchored observations.
When such observations exist, `continuity_unverified` is true: the switch is
a sequence of two observed anchors, **not proof of a direct uninterrupted
transition**. Other tenants, actors, devices and sessions do not contribute
switches or intervening counts. The return flag means only that this item was
previously observed in the same capture scope.

## Additive response fields

`linked_work_items` now includes:

```json
{
  "observed_switch_count": 2,
  "observed_return_count": 1,
  "observed_switches_with_unattributed_evidence_count": 1,
  "observed_switches_truncated": false,
  "observed_switches": [
    {
      "switch_index": 1,
      "from_item_id": "linked_item_01",
      "to_item_id": "linked_item_02",
      "from_event_id": "e0002",
      "to_event_id": "e0003",
      "from_observed_at": "2026-10-20T00:02:00+00:00",
      "to_observed_at": "2026-10-20T00:03:00+00:00",
      "return_to_previously_observed_item": false,
      "unattributed_observations_between": 0,
      "continuity_unverified": false
    }
  ]
}
```

The example abbreviates the switch list. Up to **32** switches are returned,
while the three aggregate counts remain exact for the **currently returned
linked items** and validated input observations. If `max_items` truncates the
linked-item list, observations belonging to omitted items are unattributed,
not silently assigned to displayed items. The response is a bounded
projection, not a complete organizational activity audit.

No raw organization/actor/device/session IDs, tab or clipboard tokens,
customer names, window titles or source-record refs are added to the switch
entries. Existing item IDs and event IDs already visible in the linked-work
response are reused. No new network request, connector permission, persistent
data, business write or agent authorization is introduced.

## Acceptance and remaining proof

Run from repository root:

```bash
python -m pytest -q tests/test_reconstruction_switches.py tests/test_reconstruction_segments.py tests/test_work_item_links.py
```

Synthetic acceptance covers A→B→A return, missing/unattributed activity,
cross-tenant/actor/device/session isolation, time-only rejection, bounded
truncation, and existing workflow-evidence API integration. Native
Ubuntu/macOS/Windows pytest, browser/accessibility, process collaboration,
scale, packaging/release and exact-head reviews are required before merge.

These are **observed work switches**, not verified human task boundaries,
employee productivity measurements, source-confirmed business handoffs,
policy, authorization or outcomes. Real consenting-user validation and the
preregistered family holdout are still required. Assignment F1 ≥ 0.90 and
contamination ≤ 0.05 are not claimed. No private truth, scorer or split changes.
