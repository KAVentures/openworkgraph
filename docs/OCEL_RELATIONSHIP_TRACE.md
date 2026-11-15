# R24 — Bounded multi-hop OCEL object relationship trace

OWG previously offered an exact, session-only **one-hop** explorer for the
object-to-object (O2O) assertions explicitly supplied in a retained OCEL 2.0
JSON/XML import. This adds a **two-hop / three-hop navigation slice** without
silently flattening objects into one business case.

## Use the local dashboard

1. Open **Activity → Large event logs**, select a previously imported OCEL
   dataset and choose **Analyse**.
2. Under **Supplied object relationships**, choose one literal object ID or
   select an existing source/target object in the original table/diagram.
3. Use the existing incoming/outgoing/both direction filter and optional
   exact qualifier filter. Choose **1, 2, or 3 hops** in **Follow related
   objects** and click **Trace related objects**.
4. Review the nodes, their shortest discovered hop distance, original
   directed edges, literal relationship labels and first-discovered hop.
   Select any object to inspect it using the original one-hop view. The
   selected *case notion* for process analytics is never changed.

The view contains at most 60 nodes and 100 edges through the current UI
(default). The backend allows caller-selected limits of 2–100 nodes,
1–200 edges and 1–3 hops. Once a limit is reached, the result explicitly
says that traversal is incomplete. All displayed edges retain their source,
target and exact qualifier; different qualifiers and opposite-direction
edges remain different assertions. Cycles and self-links are not discarded
or expanded indefinitely.

## API

`POST /v1/event-logs/{log_id}/object-relationships/trace`

Example:

```json
{"object_id":"order1","direction":"outgoing","max_hops":2,
 "max_nodes":60,"max_edges":100}
```

Optional `qualifier` performs an **exact** literal match. An omitted filter
means all qualifiers, while an empty string means only truly empty labels.
The body is strict JSON, capped at 16 KiB; unexpected keys, booleans in
numeric limits, unsupported depth and invalid identifiers are rejected.

The response contains `nodes`, `edges`, `shown_nodes`, `shown_edges`,
`requested_hops`, `hops_visited`, `truncated`,
`truncation_reason`, `relationship_revision`,
`retained_assertions_in_log` and a separate selected-object reference.
No page or count is claimed complete after a traversal limit. If the
source object has no links, the result contains only that object and zero
edges. A nonexistent object is refused.

The route requires OWG's **local dashboard session**. An anonymous request
or AI bearer token cannot retrieve the retained object IDs. The current log
and relationship index are read under the same workspace lock, with no
source connector reads, remote transmission, AI calls, data mutation or
business action. Results use no-store response headers.

## Interpretation and limits

- These are **caller-supplied, static OCEL O2O assertions**. They are not
  entity-resolution results and do **not** imply that the source records
  describe the same business case, were causally related, or existed at
  the same time.
- No event-to-object linking, shared-event inferred joins or case flattening
  is performed by the trace. Links may have no corresponding observed event.
- Node hop distances are navigational distances in the stored assertion
  graph, not elapsed times, process steps, workflow handoffs or verified
  process conformance.
- The trace does not infer missing links or validate source-system
  completeness. Even a non-truncated result is complete only to the
  selected hop depth **within the retained assertions**.
- The deterministic scan is intentionally bounded. Large hubs may truncate
  before the entire requested depth is explored. The traversal never
  presents incomplete results as exact full connected components.
- Import/append consent, deletion, log retention and the original
  relationship-revision semantics remain unchanged. Graph navigation
  does not create any share, manager permission or agent grant.

## Acceptance

`tests/test_event_log_object_paths.py`: six-object synthetic OCEL fixture
with forward, reverse, cyclic and self relationships; two/three-hop
reachability, strict direction and qualifier matching, disconnected
objects, node/edge budget truncation, invalid scopes and dashboard-only
route enforcement. It checks that no dangling edge is returned after
reaching a node limit.

`tests/browser/event_log_object_relationships.cjs`: real local OCEL
import, two-hop UI traversal over 34 retained assertions, safe literal
rendering (including malicious HTML-like qualifier), source navigation,
and existing keyboard, accessibility, mobile, export and deletion checks.

Native CI must pass on the exact PR head before merge.
