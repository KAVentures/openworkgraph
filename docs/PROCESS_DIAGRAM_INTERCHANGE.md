# Portable BPMN diagrams

Exact-revision catalog exports and reviewed-proposal agent starter ZIPs now
include BPMN Diagram Interchange (BPMNDI) geometry. An external viewer can draw
the map immediately, including decision branches, loops and manual-review paths.
Previously the exports described the process without specifying its visual layout.

The same shared helper adds a plane, activity bounds, optional external label
bounds and transition waypoints to both exporters. Activity/transition IDs,
revision/hash documentation, declared rules, manual conditions and non-executable
semantics are preserved. There are no generated condition expressions or default
business decisions. The initial layout is deterministic and bounded; complex
branches and cycles may benefit from further editing in a specialist modeler.
This is not full BPMN execution, business-policy validation or certification
against every external modeling product.

## Reference investigation

REA 6.7.0 statically analyzed the public **bpmn-js 18.31.0** importer and renderer:
`lib/import/BpmnImporter.js` and `lib/draw/BpmnRenderer.js`. The importer consumes
BPMNPlane, BPMNShape bounds and BPMNEdge waypoints to construct the visible graph.
That supplied a concrete interoperability requirement for OWG's own exporter.
No third-party implementation code or artwork was copied into OWG.

The local REA evidence identifier was
`ev_a9b40fd5099057392d6589d017339b83567ea5f8e7518c8236c9dd565adf983a`.
Static analysis covered only the selected files; 546 inventoried files remained
outside that analysis, and dynamic behavior was not inferred from their absence.
The reference was a public BPMN toolkit, not a private competitor application.
No claim is made about competitors' internal algorithms or runtime parity.

## Verification

Python tests verify deterministic geometry, semantic references, distinct node
positions, finite positive dimensions, loop waypoints and preservation of manual
review/non-execution semantics. Existing catalog, decision-ledger, agent-starter
and generated-project tests continue to pass.

`tests/browser/bpmn_interchange.cjs` generates synthetic OWG exports, imports them
in an actual bpmn-js modeler in Chromium, checks every activity and transition,
exports SVG/XML, and imports the resulting XML again. It covers linear paths,
branching, back edges, self-loops and unresolved manual-review starter gateways.
No customer records, network business actions or model-provider calls are used.

The external toolkit is installed only in the isolated CI/test runtime; OWG adds
no runtime dependency on it. Its distributed bundle is unmodified and keeps its
built-in attribution. The test does not certify model execution or other tools.
