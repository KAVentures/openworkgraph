# Directed object topology review (R24)

Open Evidence → saved event logs → object relationship comparison → Review
relationship connectivity and removal impact. Inspect the retained OCEL log,
choose an optional exact qualifier, and confirm the snapshot. The existing
weak connectivity, articulation and bridge views remain available.

Two new views preserve the literal direction of the selected object-to-object
assertions:

- **Directed strong components and cycles:** mutually reachable object groups,
  cyclic groups, self-loop objects, number of object types, internal and outgoing
  assertion counts, predecessor/successor group counts and source/sink flags.
- **Directed component object members:** every retained object exactly once,
  its component and type, group size, cyclic group flag, distinct incoming
  and outgoing neighbors and whether it has a self assertion.

A component ID is the lexicographically smallest literal object identifier
in that component, not a new business-case ID. IDs and membership can change
when the selected qualifier or retained snapshot changes.

All retained objects remain in the population, even if they have no selected
assertions. Parallel qualifiers on one ordered endpoint pair collapse for
graph connectivity; literal assertion counts remain separate. Self-loops
count as cycles, but not as distinct incoming/outgoing neighbors in the
member view. The condensation graph collapses each strongly connected
component to a node and is acyclic; source and sink counts refer to that
projection. Isolates are both sources and sinks. No paths or transitive
closure are materialized by the engine.

The same bounded retained snapshot, exact qualifier, explicit confirmation,
page pins and dashboard-session authorization apply to these new views. Up
to 20,000 retained objects and 100,000 retained assertions can be reviewed
without sampling. A page contains up to 100 rows; the dashboard defaults to
50. Downloaded JSON contains literal local object identifiers and must be
handled accordingly.

Relationship direction is supplied by the source or caller. It is not
necessarily temporal order, causation, workflow execution order, dependency
authority, policy, or evidence of actual business start and end points.
Cyclic components are not inherently erroneous. No operational change,
source read, relationship mutation or external authorization is performed.

## Validation

```sh
python -m pytest -q tests/test_ocel_directed_topology.py tests/test_event_log_directed_topology.py
```

The graph tests use an independent transitive-reachability oracle on 600
random graphs, explicit cycles and isolates, rejected invalid assertions,
and a 20,000-object chain and cycle. Retained-log and dashboard-session tests
exercise source fingerprints, exact qualifiers, pagination, stale snapshots,
authorization and the existing weak projection. The existing browser
acceptance also exercises both directed views.

This change is stacked on the topology workbench (#1648). Do not merge into
the parent branch or reset its CI. Integrate the parent into main, reconcile
this child against the current main tree, and require full combined-tree
validation before merging.
