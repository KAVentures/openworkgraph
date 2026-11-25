# BPMN 2.0 swimlane diagram interoperability (R13)

OWG already exports **semantic** BPMN lanes from reviewed process activity
`responsibility_label` declarations (#650). This enhancement also emits
**BPMN Diagram Interchange (DI) lane shapes and geometry** so external BPMN
modeling tools can *display* those declared responsibilities as visible
horizontal swimlanes, with the corresponding activity shapes inside each
lane. The output is still a human-reviewed **non-executable** proposal.

## User workflow

1. Open the existing process catalog and review a versioned process.
2. Assign descriptive responsibility labels to relevant activities.
3. Export the reviewed revision as BPMN XML.
4. Open the file in a BPMN 2.0 editor with Diagram Interchange support.
   The exported `laneSet` and `flowNodeRef` associations now have matching
   `bpmndi:BPMNShape` lane regions, bounds, labels and contained activities.
5. Edit positions, responsibilities and approvals in a tool that supports
   those operations. Re-importing a BPMN file remains subject to OWG's
   existing strict descriptive import rules and separate human review.

## Geometry

- When a process has one or more semantic lanes, OWG creates **one BPMNShape
  per lane** with `bpmnElement` referencing the exact lane ID and
  `isHorizontal="true"`. Each declared activity is placed completely inside
  the bounds of its assigned lane. Lane identities and membership never
  depend on guessed actor names, timestamps or business records.
- A lane with multiple activities at the same process-graph level receives
  enough vertical space to avoid node overlap. Lane order follows the
  reviewed model's order; layout is deterministic across exports.
- Activities without a declared lane are displayed in a separate unassigned
  area, **outside** named lanes. No invented "unassigned" BPMN lane is
  added to the semantic model.
- The existing `BPMNEdge` sequence flows retain their original source
  and destination and deterministic orthogonal routing. Lines can traverse
  lane boundaries; that does not imply a verified cross-team handoff.
- Models without a `laneSet` use the same deterministic graph layout without
  inventing lanes. Semantic node IDs and sequence flows are preserved; the
  geometry can differ from earlier exports.
- Malformed DI source states such as duplicate membership, unknown
  `flowNodeRef`s, more than one laneSet or unsupported nested lanes fail
  explicitly rather than dropping assignments or inventing geometry.

## Safety and limitations

The lane label remains a **user declaration**, not a verified person,
employment relationship, organizational role, permission, workflow
execution instruction, business rule or agent handoff approval. The BPMN
process remains `isExecutable="false"`.

This is a deterministic initial presentation layout, not a standards-
certified BPMN renderer or a solver for lane-size optimization. External
editors may re-layout shapes and edges. Lane geometry has no effect on
process mining, case evidence, provider integrations, capturing, AI grants
or executable agent authority.

## Validation

`tests/test_process_diagram_lanes.py` covers lane-to-shape integrity,
node containment, multiple same-level nodes, explicitly unassigned
activities, deterministic/no-lane compatibility, stable unique IDs and
fail-closed invalid references. The existing BPMN export/import,
versioned-process-catalog and process-diagram suites must also pass.
The usual six exact-head workflow groups plus Ubuntu/macOS/Windows jobs,
packaging, accessibility and scale acceptance are mandatory before merge.

This closes a specific R13 semantic-layout interoperability gap. It does
not establish full Signavio or Celonis modeling parity, which still depends
on broader customer acceptance, hierarchical process design and verifiable
outcome workflows.


## Branching and cyclic layout

The exporter now ranks strongly connected components in prerequisite order
on their acyclic condensation graph. A join after both a short and a long
branch is placed after the longer branch, rather than being pulled earlier
by a shortest-path shortcut. Loops retain deterministic local breadth-first
positions; an exit from a loop is positioned after the entire component.
Disconnected retained nodes still receive geometry.

Orthogonal routes use the full column gaps, rather than the bounds of a
narrow gateway or event. Non-adjacent and backward edges use separate upper
tracks; self-loops return through the top port. This avoids traversing the
interior of unrelated activity rectangles, including in other lanes. Edge
crossings and shared segments, text-label collisions and optimal compactness
are not solved; external editors may still improve the layout. The same
120-node and 200-flow limits remain.

Regression tests verify exact shape/flow coverage, boundary attachment,
orthogonality, avoidance of activity interiors, semantic XML preservation,
repeatable coordinates, branching joins, cyclic exits, and the maximum
supported graph size. No executable semantics or permissions are introduced.
