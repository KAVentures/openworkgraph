# Object-centric process models

An object-centric event log (OCEL 2.0) links each event to several objects: one *Place order* event involves
an order and its items. Flattening such a log onto one object type leaves events out or copies them (see the
flattening diagnostics). An object-centric process model avoids that. It is in the spirit of object-centric
Petri nets (van der Aalst & Berti, 2020) and has two parts:

- **Lifecycle models:** one model per object type: how an order, an item or a delivery moves.
- **Arcs:** which object types each activity involves.
  - A *single* arc means every event of the activity involves exactly one object of the type.
  - A *variable* arc means at least one, possibly many.

## Discovery

`POST /v1/event-logs/{id}/object-process-model/discovery` with an optional body:

```json
{"object_types": ["order", "item", "delivery"], "noise": 0.2, "activities": 1.0}
```

- **Types:** without `object_types`, the eight most frequent types are used. The rest are listed in
  `types_omitted_over_limit`.
- **Lifecycle models:** each type's model comes from the existing bounded inductive discovery, run on that
  type's objects only. `noise` and `activities` work as in [model discovery](EVENT_LOG_WORKSPACE.md).
- **Undiscoverable types:** a type whose model cannot be discovered is listed in `types_not_discovered` with its
  reason, for example a model larger than the 40-node catalog bound.
- **Arcs:** each arc is measured from the recorded links, with the minimum, maximum and mean number of
  objects per event in `arc_measurements`. An arc is variable when some event involved more than one object.
- **Labels:** model labels are normalized to the catalog's label rules. `activities` maps each label that
  differs back to its exact activity name, so replay never matches names approximately.

The response's `model` is a draft for review, not a verified workflow.

## Conformance

`POST /v1/event-logs/{id}/object-process-model/conformance` with `{"model": …, "max_variants": 200}`. The model
can be the discovered draft or an edited one, for example with an arc changed from variable to single. The
body may be up to 256 KiB, because eight types at the catalog bound take about 110 KB.

- **Lifecycles:** each object's events, in time then event-ID order, are token-replayed on its type's model,
  using the same replay as conformance against a saved process.
  - Each type reports fitting objects, token fitness, and missing and remaining tokens by activity.
  - An activity the type's model lacks counts as unmodelled, which means missing tokens. It is never ignored.
  - Objects with more than 300 events, beyond `max_variants` variants, or past the replay budget are counted as
    unknown, by reason, instead of being guessed.
- **Arcs:** each event of a modelled activity is checked against its arcs. Three issues are counted separately,
  with up to 20 examples:
  - an event without an object of a required type (`missing_object`);
  - several objects on a single arc (`several_objects_on_single_arc`);
  - an involved type the activity has no arc for (`type_without_arc`).
- **Summary:** the fitting objects and fitting events overall.

## Limits and interpretation

- **Projection-based:** lifecycles are replayed per type, and how types meet is checked through the arcs
  (`binding_based_replay: false`). This does not check that the same objects move together through several
  shared activities. [Object rules](EVENT_LOG_WORKSPACE.md) check that, for example with
  `synchronization`.
- **Bounds:** up to eight types per model, 400 arcs, and the catalog bound of 40 nodes, 100 transitions and 40
  rules per type.
- **What results mean:** results describe the recorded links of the imported log. A deviation may reflect a
  missing event or link. Business rules, policy compliance and outcomes are not evaluated.

## Dashboard

The object-centric map has an **Object process model** section next to Object rules:

- **Discover object model** shows each arc with its measured objects per event and a choice: *Exactly one*,
  *One or more* or *Not involved*.
- **Check object model** sends the edited model. Its results show fitting objects and token fitness per type,
  where tokens went missing, arc issues and examples.

## Validation

- **Order-to-cash fixture:** every count was checked by hand. A discovered model fits its own log: 9 of 9
  objects and 11 of 11 events.
- **Deviating log:**
  - an order paid before it was placed, and an order paid twice;
  - an item never picked (a missing *Pick item* token before *Ship*);
  - a delivery in a *Pay* event, which is unmodelled for deliveries and also a type without an arc;
  - a *Pay* event with no order (missing object) and one with two orders (several objects on a single arc).

  Each is found and counted.
- **Other checks:**
  - normalized labels map back to exact names, and without the mapping the activity is reported as unmodelled;
  - long lifecycles and the variant limit are counted as unknown;
  - invalid models and settings are refused with a 422.
- **Routes:** they need a dashboard session and return `no-store`. Repeated JSON keys are refused, and the 256
  KiB body limit applies.
- **Scale:** on a synthetic order, item and delivery log, measured locally:
  - 54k events and 45k objects: discovery 0.9 s, check 0.9 s;
  - 540k events and 450k objects: discovery 3.6 s, check 4.2 s.
