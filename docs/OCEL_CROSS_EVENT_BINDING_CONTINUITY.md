# Same-pair cross-event binding continuity for OCEL 2.0 (R24)

Object-centric logs routinely link one business event to several objects. A
single case-flattened analysis cannot reliably prove that **the same order and
item** jointly participated in both a `Pack` event and a later `Ship`
event: the two objects may participate in separate events at the same time,
and an item may be shared by several orders.

OWG now has a bounded, local query for this evidence gap. It is **not** a full
object-centric Petri-net (OCPN) binding replay.

## Analyst workflow

1. Import an OCEL 2.0 JSON/XML log with the existing explicit local-storage
   confirmation. No cloud source connection or extra recording is needed.
2. From the local **Evidence** workspace, open **Inspect OCEL joint-event
   bindings**.
3. Choose a saved OCEL log, a caller-supplied directed source→target object
   relationship, the optional exact qualifier and two different activities,
   e.g. `Pack` then `Ship`.
4. OWG considers one distinct directed source/target object pair at a time. A
   selected event counts only if its **same original event ID** is linked to
   *both* object IDs. Events merely having the same activity or timestamp
   on separate objects never create a witness.
5. The explorer groups all selected pairs into mutually exclusive outcomes:
   - `forward`: both witnessed and earliest second event after earliest first;
   - `reversed`: both witnessed, second timestamp earlier;
   - `same_timestamp_order_unknown`: both witnessed but timestamps equal;
   - `first_only`, `then_only`, or `neither_joint_event`.
   It also reports repeated shared events for either activity, number of
   **distinct original joint events**, pair-event witnesses, unique source
   and target objects, and descriptive elapsed time for forward pairs only.

A related target item can occur in several supplied relationships; that
rightly counts once per *pair*, not once per target. Multiple qualifiers
for the same O2O relation or event-object association never inflate the
number of pairs or their event counts. Self-referential O2O links cannot
prove the co-attendance of two distinct objects and fail the entire selected
cohort instead of counting the same object twice. The UI exposes the two
distinct-object counts to prevent interpreting pair-level metrics as independent items.

The query leaves saved data unchanged, uses the existing **local dashboard
session** (not the generic MCP bearer), and supports only literal values
from imported data. Queries beyond a limit fail rather than returning a
partially analyzed cohort: at most 20,000 directed pairs, 1,000,000 selected
distinct event/object links, 1,000,000 joined pair-event witnesses and 50
displayed example pairs.

## API

```http
POST /v1/event-logs/{log_id}/object-bindings/continuity
Authorization: OWG-Session <local-session>
Content-Type: application/json

{
  "source_type": "order",
  "target_type": "item",
  "first_activity": "Pack",
  "then_activity": "Ship",
  "qualifier": "fulfils",
  "sample_limit": 20
}
```

`qualifier=null` (or omitted) considers all qualifiers. An **empty**
qualifier string matches only edges whose qualifier is empty. The request
has a strict 16KiB JSON limit, rejects duplicate keys and nonfinite
numbers, and enforces the existing dashboard-session/host security guard.

The result pins the local log append revision, the whole-graph relationship
digest and the object index digest. It does not export raw objects to an AI
connector or create remote automation actions.

## What is not proved

- Imported OCEL O2O assertions are static. There is **no observed validity
  period** showing that a relationship existed when either event happened.
- A joint event is not proof that the event represented a legitimate
  fulfillment, commercial approval, policy-compliant workflow, customer
  outcome or causal handoff. The supplied source records are **not**
  independently verified.
- `forward` means earliest selected joint timestamps appeared in that
  order, not that *every* instance was ordered. Repetitions are counted.
  Equal timestamps have unknown business ordering even if event IDs
  can be sorted.
- This is **not** a model-based OCPN token replay or a complete binding
  conformance verdict. To enforce object-centric policies, a reviewed
  process model, full event bindings, and independent source checks
  remain necessary.
- No source provider, runtime executor or business system is contacted.

## Tests

- `tests/test_event_log_object_binding_continuity.py`: nine manually
  countable directed pairs with two qualifiers on one edge, a shared
  item among orders, repeated events, reversed and equal timestamps,
  zero-event objects, and the key **negative** case: the same two objects
  have individually recorded Pack/Ship activities at identical times but
  never attended the same original event. Also tests strict validation,
  fail-closed work budgets, read-only snapshots and real HTTP access.
- `tests/browser/object_binding_continuity.cjs`: OWG Evidence → local
  explorer in a real Chromium browser, authenticated OCEL import, precise
  joint event witness counts, stale filter invalidation, unauthenticated
  access refusal, Axe and 390px mobile viewport.
- `.github/workflows/explorer-accessibility.yml` now runs the new
  browser acceptance test as part of the required explorer workflow.

This is an additional R24 capability. It makes no claim to fully close
roadmap #370 or replace independent production pilot validation.
