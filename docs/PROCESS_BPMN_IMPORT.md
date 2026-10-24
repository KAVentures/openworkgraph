# Bring a BPMN design into the process catalog

Open **Evidence → Process catalog → Import a BPMN design as an unreviewed
draft**. Choose an authorized UTF-8 `.bpmn` or `.xml` file and explicitly select
**Preview selected BPMN**. Selecting a file makes no request. Preview parses
the selected file through the authenticated local dashboard API, without saving
a design, recording source evidence, calling providers or executing XML.

The supported BPMN 2.0 subset is one process containing plain start/end events,
tasks, exclusive gateways and supported paired parallel gateways connected by
sequence flows. An exclusive gateway with
multiple outgoing routes becomes a decision with unconfirmed branch descriptions;
a gateway with one outgoing route becomes a merge/routing node. Routing nodes
are not source-log activities and need no business-tool mapping in generated
projects. Import supports exclusive loops when the graph has an unambiguous
entry and a route to a terminal.

Nested or conditional parallel regions, inclusive gateways, subprocesses, event triggers, message collaborations,
external imports, executable extensions, scripts and loop characteristics are
rejected. Their semantics are not flattened into an incorrect serial graph.
Tasks with implicit branching are rejected; use an explicit gateway.
[Parallel regions](PARALLEL_PROCESS_MODELS.md) require disjoint linear branches
and a complete paired join. Their descriptive exports and structural analysis
preserve all branches; the current linear agent-project exporter refuses them.
A process needs one plain start event, or one structurally unambiguous entry
when no start event is supplied. All imported nodes must be reachable and able
to reach a terminal. Multiple processes, ambiguous entries, duplicate IDs or
transitions, disconnected fragments and invalid references are rejected.

## Review and save

Preview displays the graph, unconfirmed rules, warnings, labels needing correction
and bounded source documentation. **Open imported draft in editor** makes a
separate unsaved model. Review its business goal, structural labels, exclusive
branch rules, responsibilities and any manual-review guards; then use the
existing exact-graph review and Save action. Opening a draft never saves it.
Editing or replacing files invalidates pending previews; replacing the current
catalog draft retains the existing discard-confirmation control.

The imported baseline is explicitly `user_declared_design`, with zero observed
support and no asserted observed execution. It is not a reconstructed AS-IS
case path. All implementations start `unmapped`, all branch descriptions start
`user_confirmed: false`, source-state expectations are empty and execution/outcome
flags remain false. An input `isExecutable=true` flag changes none of those facts.
No script, condition language, source endpoint, credential or tool implementation
is executed or configured. Condition expressions and branch documentation are
untrusted descriptive text for human review, never evaluated policy.

Source XML IDs map to new positional graph identifiers. Original IDs and source
file hashes are returned only in the ephemeral preview; original IDs are not
saved or encoded as guessable hashes in the model. Missing or unsupported labels
receive ASCII structural placeholders, with bounded original labels shown for
human correction. Arbitrary HTML-looking labels and documentation stay inert
text, never HTML or agent instructions.

OWG's own exported, explicitly formatted responsibility labels and unevaluated
manual-review conditions are preserved through import as descriptive declarations
and nonexecuting guards. Their authenticated actor, organization roles, source
identity, design-review status and execution permissions are never imported.
Malformed recognized OWG review documentation is rejected rather than silently
dropping a guard. Generic source documentation is shown separately and not
automatically saved into knowledge. If unstructured documentation or placeholder
labels need interpretation, a **Review original BPMN documentation and labels**
manual guard is attached to the entry. Generated planners must request human
clarification until an implementer explicitly resolves that guard through a
reviewed model update. An overall design-review checkbox does not remove it.

Source diagram geometry is ignored and recomputed by the shared OWG renderer.
Exported BPMN includes portable geometry and remains `isExecutable=false`.
The imported exclusive merge is exported as an exclusive gateway, preserving
its routing role. Full business-policy validation, nested/conditional parallel semantics,
interprocess messages and executable BPMN deployment are outside this increment.

## Bounds and API

`POST /v1/process-catalog/import-bpmn` requires a local dashboard session and XML
content (`application/bpmn+xml`, `application/xml` or `text/xml`). Ordinary AI
bearer credentials cannot upload a design here. The actual request stream is
bounded to 128 KiB. XML must use UTF-8; DTD/entity declarations, malformed XML
and invalid control bytes are rejected. There is no external XML resolution.

Bounds: 3,000 XML elements, 40 graph nodes, 100 transitions, 40 rule descriptions,
12 manual guards, 40 separate documentation notes, 200-character XML IDs and
2,000-character branch descriptions. Documentation notes display at most 2,000
characters each, with explicit truncation flags; label corrections show at most
240 characters with their own truncation flag. Existing strict process-model
validation runs before any preview is returned. Errors use fixed messages that
do not echo source payloads. Successful responses use `Cache-Control: no-store`.

The response `openworkgraph.bpmn-draft-import.v1` contains the unreviewed model,
source SHA256, ephemeral source-ID mapping, label corrections, documentation
notes, warnings and explicit false retention/execution/outcome flags. A separate
catalog Save creates an immutable reviewed-knowledge revision. No new data store,
normal observation, AI-access mode, sharing permission or automatic deployment
is introduced. Unmodified existing models keep their previous serialization and
hashes; routing and declared-design coverage are additive model values.

Validation covers branch/merge language, loops/terminal constraints, generated
agent mapping requirements, source IDs, inert labels/notes, preserved manual
guards/responsibilities, malformed/unsupported XML, bounds, authentication and
no persistence during preview. Actual Chromium exercises preview → edit → review
→ save → nonexecuting export and mobile. The external bpmn-js modeler imports,
exports and reimports an imported exclusive-merge fixture with finite geometry,
correct endpoints, no import warnings and no execution enablement.
