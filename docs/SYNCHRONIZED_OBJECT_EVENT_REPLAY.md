# Synchronized original-event object replay (R24)

This workbench advances the object-centric conformance path beyond independent
greedy lifecycle projections. Every original retained event is checked atomically
against every participating selected object. Each lifecycle keeps its complete
set of reachable structural markings within explicit bounds; silent transitions,
exclusive choices, and supported paired parallel/inclusive blocks use the shared
edge-token semantics. No missing token is inserted to make an event pass.

## Operator workflow

1. Open Activity → Evidence → **Replay synchronized object events**.
2. Select a retained OCEL log. Discover a draft or import an existing object
   process model JSON and validate it against the destination's object types.
   Use an independently learned model for out-of-sample review.
3. Run synchronized replay. Review the complete selected population and the
   counts of connected components, events and object terminal states.
4. Filter components by complete, unfinished, deviation, unknown or no events.
   Inspect exact original event IDs, first demonstrated failures, linked-object
   cardinality, blocked lifecycle examples, and paginated participant evidence.
5. Export the summary or selected evidence page. Each export re-runs the
   review against the pinned model and retained source basis.

The model is not silently modified, saved or promoted to an approved design.

## Semantics

- A component is connected through recorded memberships of original events,
  not inferred object-to-object relationships. An event belongs to one component.
- The selected population comprises every registered object of a modeled type
  and every raw retained event linked to one of them or carrying a modeled
  activity. Other event/object counts are reported as excluded.
- Qualifier multiplicity never duplicates participants. Required arcs demand
  one or more objects; a single arc demands exactly one. Linked modeled types
  without an arc and unmapped activities are explicit shape deviations.
- For each event, evaluate every participant's possible lifecycle markings.
  Commit all marking updates together only if all participants can advance.
  Stop at the first demonstrated deviation of the component. Later events are
  separately counted as not replayed.
- When every event is accepted, check whether every object's marking can reach
  its terminal by silent firings. An accepted but unfinished prefix is not a
  successful complete trace and is not evidence of a business failure.
- Distinct events with the same timestamp and a shared modeled object make the
  entire component unknown. Events with disjoint participants commute under
  these model semantics. No arbitrary event ID tie-break is used to assert
  chronology. Isolated registered objects are separately counted.
- Budget exhaustion is unknown, not deviation. All selected events and objects
  are accounted for once. No population is sampled.

This is exact bounded synchronized replay of the supplied projection model,
not general object-centric Petri-net replay. Arbitrary multi-object places,
object creation/transfer, qualifier-specific bindings, object-to-object
relationships, unobserved event insertion, optimal alignments and actual
business policy or outcome verification are outside this model.

## API and work bounds

POST /v1/event-logs/{log_id}/object-process-model/synchronized-replay
requires a local authenticated dashboard session, strict JSON and an existing
object process model. The same endpoint supports:

- view: summary (default), components, events, objects or participants
- expected_review_sha256: mandatory for all evidence views
- component_id: exact reviewed component digest for events/objects/participants
- event_id: exact original event ID for participant evidence
- status: view-specific filter; offset and limit for bounded pages

The source pin covers the model digest, selected object IDs and types, exact
original event IDs, timestamps, activities, distinct memberships, retained
totals and replay bounds. The pin excludes unrelated attributes and qualifiers.
Changes to the pinned basis require a fresh summary. It is not a signature
or provider verification. Analysis reads a snapshot under the workspace lock;
there are no writes to the model, event log or external systems.

Complete-population limits: 30,000 events, 30,000 objects, 150,000 distinct
memberships. An oversized population is refused, not truncated. Marking sets
are limited to 512; global search work to 2,000,000 operations and per-component
work to 200,000. Budget changes affect the pin. Evidence pages are bounded to
100 rows and blocked object examples to 20 per event; full distinct participant
membership is available by page. No enterprise-scale performance claim.

## Acceptance

- `tests/test_event_log_synchronized_object_replay.py`: real retained OCEL
  order-to-cash imports; exact event and object counts; independent component
  continuation; atomic failures; missing and excessive binding cardinalities;
  unmapped activities; disjoint and shared timestamp ties; terminal prefixes;
  isolated objects; qualifier multiplicity; bounded uncertainty; exact
  nondeterministic alternatives; source/model drift; strict HTTP and scope.
- `tests/browser/object_binding_continuity.cjs`: actual dashboard session,
  model discovery, original-event replay, deviation component and participant
  drill-down, invalidation, mobile layout and accessibility.

Native CI and browser execution remain required before release. A valid
structural replay does not grant authorization or prove a business outcome.
