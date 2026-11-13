# Temporal target-object attributes at related-object milestones (R24)

OWG imports OCEL 2.0 object relationships and preserves the full timeline of each
object's typed attributes. The relationship-timing explorer previously measured
the first activity times across two supplied, directed related objects, but
could not answer a basic many-to-many question:

> What was a related **invoice** object's recorded `status` at the moment its
> **order** first reached `Approve`, and what was its status at the invoice's
> own first `Settle` event?

The **Target attribute at each object's milestone** section of the local
`/object-relationship-lead-times` explorer implements this as a bounded
retrospective query. It is intended to retain many-to-many semantics without
flattening invoices into duplicated order cases.

## Observable journey

1. Import a caller-provided OCEL 2.0 JSON/XML file using the existing
   explicit local-storage permission. Attribute histories and static O2O
   relationships are retained as separate indexes.
2. Open the local relationship timing explorer from the event-log workspace
   and choose the exact saved source/target object types, relationship
   qualifier and first source/target activities (e.g. order → invoice,
   `billed`, `Approve`, `Settle`).
3. Enter an exact target attribute name (e.g. `status`) and click
   **Inspect attribute at milestones**.
4. The table and aggregates compare the target object's recorded value
   **as of the first selected source event** with its recorded value
   **as of the first selected target event**.

Each **distinct directed** (source ID, target ID) pair contributes **one**
observation, even if an OCEL edge has several qualifiers or an event links to
an object under multiple qualifiers. A target object linked to two source
objects rightly contributes twice, once for each independent supplied pair;
the UI explicitly reports both distinct source and target object counts, so
these are not misrepresented as independent invoices.

The query classifies each pair at each milestone as:

- `known` — one latest recorded attribute value at or before that event;
- `attribute_recorded_only_later` — a future value exists, but it cannot
  safely be backfilled onto an earlier event;
- `attribute_never_recorded` — no recorded value for that target object;
- `ambiguous_same_effective_time` — several distinct values share the
  most recent effective time, so one cannot be selected;
- `source_activity_missing` or `target_activity_missing` — no selected
  event was observed for that side.

A value timestamp equal to an event timestamp **does apply**. The OCEL
initial-value convention `1970-01-01T00:00:00Z` is counted separately:
it means the value is supplied as an initial state **without evidence of
its real-world start time**. Contradictory same-time values are not silently
resolved. Reported changes across two milestone timestamps are *differences
between recorded states*, not proof that the selected event caused a change.

## Result coverage and provenance

The response supplies counts over the complete selected pair cohort, including
unknown/missing categories, the most common 20 value/state groups and up to 50
examples of local object IDs with recorded values/effective times. No case is
silently dropped from the denominator because of absent state history.
These are **locally authenticated dashboard data**, not an AI capability,
public API or cross-tenant export.

A request refuses rather than computing a partial cohort if it exceeds
20,000 distinct directed pairs, 1,000,000 selected distinct event links,
or 250,000 relevant recorded attribute rows. It uses exact literal
type/activity/attribute/qualifier filters (optional qualifier `null` means
all qualifiers, the empty string means only empty qualifiers).
The API is:

```http
POST /v1/event-logs/{log_id}/object-relationships/attribute-milestones
Content-Type: application/json
Authorization: OWG-Session <local-session>

{
  "source_type": "order",
  "target_type": "invoice",
  "source_activity": "Approve",
  "target_activity": "Settle",
  "target_attribute": "status",
  "qualifier": "billed",
  "sample_limit": 20
}
```

It is capped at 16 KiB JSON, rejects duplicate JSON keys and non-finite values
via the existing strict local event-log route parser. Existing relationship
snapshots supply the relationship and object-index hashes and append revision.
The query does not modify saved logs or background schedules.

## Critical limitations

- **Static relationship validity is unknown.** OCEL O2O assertions supplied
  without validity intervals do *not* establish that the relationship existed
  when either milestone event occurred. This feature is explicitly NOT an
  as-of relationship-membership join, despite reading object attributes as-of.
- Imported source timestamps, attribute histories, relationship direction and
  qualifiers are caller data, **not independently verified business facts**.
- A shared target contributes several pairs; pair-level rates must not be
  interpreted as object-level prevalence or independent invoice-level effects.
- The query does not prove causality, actual process conformance, savings,
  business outcome, current state, or deployment permission. No actions are
  planned, authorized or executed.

## Tests

- `tests/test_event_log_related_attribute_milestones.py`: OCEL import,
  many-to-many shared invoices, duplicated qualifiers, values exactly at
  a milestone, out-of-order history, future-only values, absent values,
  simultaneous ambiguous values, reversed activity times, distinct-pair
  accounting, work budgets, snapshot immutability, and real local HTTP auth.
- `tests/browser/object_relationship_lead_times.cjs`: browser comparison
  against synthetic target attribute histories, direct page display,
  stale-result invalidation, accessibility and mobile width.

Full repo test and browser CI is required before merging.
