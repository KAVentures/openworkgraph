# BPMN semantic lanes from declared responsibilities (R13)

OWG's version-pinned reviewed process exports now include actual BPMN 2.0
`laneSet`, `lane` and `flowNodeRef` elements whenever the saved
`ProcessModel` carries user-declared `responsibility_label` values.

Previously, exporter output contained only per-activity descriptive
`documentation` notes. Most BPMN tooling cannot interpret those notes
as structural swimlane assignments. The new export is interoperable with
tools that understand the standard BPMN 2.0 process/lane schema, while
preserving OWG's separate review and execution controls.

## User-facing workflow

1. In the existing OWG versioned process catalog/designer, create or open
   a reviewed process revision.
2. Set a **declared responsibility** for each activity that needs a lane,
   such as `Finance`, `Operations` or `Customer Support`.
3. Save a reviewed revision through the existing catalog approval flow.
4. Export the process as **BPMN**. The exporter groups activities with
   identical declared labels into stable lanes and references the original
   graph nodes using `flowNodeRef`.
5. Review the BPMN in an external process designer. Re-importing the OWG
   BPMN into OWG preserves the declared responsibility text, but does not
   establish any verified identity, business policy or permission to execute.

All existing sequence flow, branch rule, BPMN Diagram Interchange geometry,
version/revision SHA and non-executable process guardrails remain unchanged.
The lane set is generated in the BPMN model section; lane rectangle layout
is **not** currently drawn in BPMN DI and may be arranged by the recipient
tool. An unlabeled activity is not assigned to an invented "unknown" lane.

## Exact semantics and safety

- One `laneSet` is emitted when at least one responsibility label exists;
  none otherwise, preserving the previous exports for unlabeled processes.
- Exactly one lane is created per **distinct literal declared label**, with
  `flowNodeRef` entries for each member activity. No activity is duplicated
  into multiple lanes. Lane order follows first appearance in the stable
  reviewed graph, never an unordered map.
- The process is still `isExecutable="false"`. Every lane includes a
  documentation statement that the label is **not a verified employee,
  identity, organizational authorization, approval or execution grant**.
  Existing activity notes also retain the label's origin.
- XML values are escaped by ElementTree. User labels are never treated as
  XML markup, scripting, source-system instructions, or executable code.
- No new provider calls, local recording, consent scope, source data
  sharing, cloud synchronization or AI-triggered execution is added.
- Neither BPMN lanes nor internal role labels prove who carried out
  historical work. Validated source-level responsibility assignment and
  actual process/agent permissions remain separate backlog items.

## Validation

`tests/test_process_bpmn_lanes_export.py` checks deterministic output,
correct BPMN namespace, one lane per label, exact flow node assignments,
XML escaping, no-label compatibility and OWG import/export roundtrip. The
existing process catalog, BPMN parser, native test matrix and release
packaging checks must also pass before merging.

This is one scoped R13 modeling/interoperability capability, not full
Signavio/Celonis process-modeling parity or a BPMN execution engine.
