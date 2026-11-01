# Conservative two-sided focus in a multiplexed tab

Roadmap: [#326 Section A](https://github.com/KAVentures/openworkgraph/issues/326),
[#264 Stage 2](https://github.com/KAVentures/openworkgraph/issues/264).
Implementation: `server/reconstruction_tab_focus.py` and the existing
`server.work_item_links.linked_work_items` read-only projection.

## What is newly usable

A browser tab can contain multiple unrelated records. OWG previously left
**every** unreferenced observation in that tab unassigned, even when a complete
observed span showed record A, an unreferenced browser step, and record A again
before any other event. The reconstruction engine now admits that strictly
bracketed step into A's already-observed linked item.

Example in one complete tenant/actor/device/session scope:

```text
e01  tab T  browser app X  exact record A
e02  tab T  browser app X  no record reference
e03  tab T  browser app X  exact record A
e04  tab T  browser app X  exact record B
```

The linked item for A includes e01, e02, e03 with
`same_tab_bracketed_record` as a distinct membership basis. B is **not**
joined. This also works for up to eight consecutive loose steps between
matching anchors. The existing workflow-evidence response exposes
`observed_bracketed_tab_focus_links`, the new link basis and conservative
confidence 0.72; there is no new endpoint, capture, permission or sync.

## Evidence gates and abstention

The two A anchors must be literal exact-record observations of the same
provider/kind, with an identical valid tab, app, source and **all four present**
organization/actor/device/session identifiers. The loose steps must be
unreferenced, without transfer tokens, malformed actions, contradictory
ephemeral claims or tainted source identity. All rows in the span must be
**globally consecutive** in validated UTC observation order, with strictly
increasing timestamps. The tab must actually be multiplexed (another exact
record exists elsewhere in the same scoped tab), and the span is capped at
eight loose steps. The pre-tab union component must not already contain a
different record or have another competing tab target.

The following **do not** gain membership: A → B → loose → A, A → loose →
Terminal → A, A → loose → B, changed app/source/tab, missing actor/device/
session, timestamp ties, long unbounded spans, competing clipboard-linked
records or any proximity-only event. Existing candidate-only multiplexed
links remain available for ambiguous events. Time gaps are never positive
evidence; no title, clipboard contents, URL or private customer label is read.

This is **observed focus evidence**, not a verified task, customer case,
handoff, completion or authorization to perform an action. False positives
remain possible if capture omitted an intervening event. Only consenting,
retained observations can contribute; revocation and retention still govern
the underlying workflow-evidence read.

## Acceptance and measurement

Run `pytest -q tests/test_reconstruction_tab_focus.py
tests/test_work_item_links.py` on native CI and the complete required
Ubuntu/macOS/Windows, browser, process collaboration, scale, accessibility,
starter and packaging/release matrix. The new tests cover a working
workflow-evidence response, multi-step recovery, cross-record/interleaved
rejection, incomplete scope, source/app/tab changes, timestamp ties,
clipboard ambiguity and no private-label leakage.

Preregistered `evals/reconstruction` family holdouts must be scored with
the original unchanged truth. Report identifiable assignment F1,
cross-workflow contamination and interruption rejection before claiming a
lift. **F1 >= 0.90 and contamination <= 0.05 have not been established by
this feature**. Synthetic acceptance is not real consenting-user validation.
