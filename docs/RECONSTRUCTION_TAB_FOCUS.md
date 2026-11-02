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

### Independently identified concurrent tabs

A bounded `A (tab T) → loose (tab T) → C (tab U) → A (tab T)`
span can now recover the loose T observation **even when tab T also contains
a separate record B elsewhere**. The intervening C observation must be in an
explicitly different, fully scoped tab of the same app and source. It is never
attached to A. The distinct
`same_tab_interleaved_bracketed_record` basis has conservative confidence
0.68 and is counted in `observed_interleaved_tab_focus_links`.
At most eight loose T events and sixteen distinct-tab observations may
intervene; ties, unknown/tabless events, changed capture scope, changed
app/source, tainted identity and clipboard actions cause abstention.

### Cross-application case interruptions (single-record browser tabs)

An exact record in a **different named application**, including a tabless
desktop observation, is now an independent negative case boundary when its
organization, actor, device and capture session all match the browser tab.
Previously, a browser tab containing only A could attach a later unreferenced
click to A even after the same actor had explicitly worked on B in Terminal.
The engine now refuses that one-sided attribution unless it sees a strict
same-tab return to A. Different-tenant or unknown-app observations cannot
invent a competing case. A source-confirmed clipboard link already joining
A and B is not a competing case.

Example with the same complete capture scope:

```text
e01  Browser tab T  exact record A
e02  Browser tab T  no record reference
e03  Terminal       exact record B (no browser tab)
e04  Browser tab T  no record reference
e05  Browser tab T  exact record A
```

Only e02 and e04 gain membership in the **existing A item**, with evidence
basis `same_tab_single_record_cross_app_bracketed` and conservative confidence
0.62. B remains separate. The existing read-only workflow-evidence response
exposes `observed_single_record_cross_app_focus_links`; these recoveries are
subtracted from `ambiguous_single_record_tab_interleaving_abstentions`.
Without e05, neither loose step can be attributed solely from the tab.
A cross-app interruption never supplies a positive case link.

A cross-app witness requires a literal different, known app label, a valid
exact record and complete matching capture scope, a distinct *pre-tab*
record-link component, strictly ordered observations, and a two-sided A
bracket. Cross-app events must not claim clipboard transfer, copy/paste or
malformed action; unknown surfaces, timestamp ties, intervening ambiguous
events and changed capture scope abstain. At most eight loose steps and sixteen
foreign observations can be crossed. The independent-record boundary index
uses per-scope/per-app/per-component sorted positions and bounded bisects;
it does not scan the entire trace for every loose event. No elapsed-time
threshold, URL, window title, clipboard contents, inferred handoff or
permission change is involved.

### Interruption-aware nominally single-record tabs

A tab showing only exact record A is not proof that all later unreferenced
activity belongs to A. When an **independent** exact-record component B is
observed in a **different explicitly identified tab and the same complete
capture scope** between the loose event and either nearest same-tab A anchor,
OWG now withholds the old one-sided
`same_tab_single_record` attachment. A bounded, strictly ordered
`A (tab T) → loose (tab T) → B (tab U) → A (tab T)` span can still
recover the loose event through two independent A anchors, with a weaker
`same_tab_single_record_interleaved_bracketed` basis and confidence 0.66.
A → B → loose with no observed return to A remains unassigned. Foreign B
is never linked to A merely by switching tabs. A source-confirmed clipboard transfer that already joins
A and B does not count as a competing component; an unknown/tabless record
does not masquerade as an explicitly identified other tab. A same-instant
foreign-record observation at a loose step or neighboring anchor is
ambiguous regardless of deterministic event-ID sort order and therefore
withholds one-sided tab membership.

The read-only response exposes
`observed_single_record_interleaved_focus_links` and
`ambiguous_single_record_tab_interleaving_abstentions`. The latter counts
one-sided tab attachments withheld without an accepted focus witness; it is
**not** a count of verified missed tasks. Other-tenant observations do not
veto this tab's scope. When there is no same-scope foreign record between the
loose step and its neighboring A anchors, the legacy single-record tab
behavior remains unchanged.

## Evidence gates and abstention

The two A anchors must be literal exact-record observations of the same
provider/kind, with an identical valid tab, app, source and **all four present**
organization/actor/device/session identifiers. The loose steps must be
unreferenced, without transfer tokens, malformed actions, contradictory
ephemeral claims or tainted source identity. All rows in the span must be
strictly ordered in validated UTC observation order, with strictly
increasing timestamps. They may be globally consecutive or interleaved
**only** with explicitly different, complete-scope tabs of the same
app/source; any other intervening evidence abstains. For the original
multiplexed-tab recovery, the tab must actually show another exact record
elsewhere in the same scoped tab. The span is capped at eight loose steps
and sixteen foreign-tab events. The pre-tab union component must not already contain a
different record or have another competing tab target.

The following **do not** gain membership: same-tab A → B → loose → A,
A → loose → Terminal → A, A → loose → B on the same tab,
changed app/source/tab, missing actor/device/session, timestamp ties,
unknown or tainted foreign-tab events, long unbounded spans, competing
clipboard-linked records or any proximity-only event. A separately
identified foreign tab is a *permitted interruption*, not a source of
positive membership evidence. An incomplete or stale capture can still
cause false attribution; absence of an observed competing case is not
proof that one did not exist. Existing candidate-only multiplexed
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
workflow-evidence response, cross-app Terminal/browser case interruption
and strictly bracketed recovery, no-return abstention, same-component
clipboard exceptions, tenant isolation, ties, malformed actions, retained
observation removal and bounded foreign spans, multi-step recovery, cross-record/interleaved
rejection, incomplete scope, source/app/tab changes, timestamp ties,
clipboard ambiguity, interruption-aware one-sided abstention, bounded
two-sided return across distinct tabs, cross-tenant exclusion, and no
private-label leakage.

Preregistered `evals/reconstruction` family holdouts must be scored with
the original unchanged truth. Report identifiable assignment F1,
cross-workflow contamination and interruption rejection before claiming a
lift. **F1 >= 0.90 and contamination <= 0.05 have not been established by
this feature**. Synthetic acceptance is not real consenting-user validation.
