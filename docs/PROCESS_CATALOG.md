# Versioned process catalog and structural comparison

BPMN exports include [portable diagram geometry](PROCESS_DIAGRAM_INTERCHANGE.md)
so external modelers can render activities, branches and loops immediately.

The local Evidence page now connects the CSV designer to the existing
`workflow_knowledge` store. No second model database or collector is introduced.
Saving is explicit; selecting or analyzing a CSV still retains no import.

An exact saved JSON export can also be explicitly published to the Gateway's
[shared organization catalog](PROCESS_COLLABORATION.md). Publication creates
separate authenticated ownership, collaborator roles and revision-specific
design reviews. It excludes local evidence references and does not automatically
synchronize private knowledge or carry local owner labels into permissions.

Decision and exception ledger entries transfer from the designer to stable
activity IDs in the catalog, including when labels repeat. The editor preserves
them as **Unverified manual review conditions**, separately from proposed branch
rules. Removing their activity removes its attached conditions; other graph
edits retain their stable references. They appear in shared-design previews,
revisioned JSON, descriptive BPMN activity documentation, PDDs and agent projects.
Their triggers remain unevaluated. A generated planner must request human
clarification for a route containing a manual review condition, even if its model
proposes a route. No condition becomes automatic branching or policy approval.

## User flow

1. Analyze an authorized CSV, choose a complete variant, and open the designer.
2. Edit the proposed path and goal, review the exact draft, then select
   **Save reviewed draft to process catalog**.
3. In the catalog, edit the title, declared owner, activities, implementation
   suggestions, transitions, decision rules and exceptions. Activity IDs preserve
   repeated labels as distinct positions. Decision branches must reference rules.
4. Review the graph and save a new revision. Every changed field clears review.
5. Reload **saved designs** to reopen after a refresh. Revision history exposes
   immutable older versions; saving an old or concurrently changed revision
   returns a conflict. Reopen the latest version and reconcile the edits, or
   explicitly save as a new process.
6. Export the saved revision as JSON, structural BPMN 2.0 or a plain text process
   design document. Unsaved edits are excluded. Each export identifies its
   workflow ID, revision and content SHA256.
7. Select **Compare next CSV with this saved revision**, choose a CSV, and
   analyze. The comparison covers every full case path, including cases and
   variants omitted from the display sample.

**Refresh graph preview** updates the graph after field edits. Graph nodes can
be clicked or activated with Enter/Space to focus their corresponding editor.
Saving validates IDs, references, duplicate edges, entry/terminal nodes,
reachability, branch rules and paths to a terminal. Loops are supported when
there is a route to a terminal. Up to 40 activities, 100 transitions and 40 rules
are allowed. A graph revision request is bounded to 128 KiB.

## Meaning and limits

The baseline is a user-selected structural observation. The target is a
user-reviewed proposal, and rule confirmation is a client declaration. These
are not organization policy attestations, actual employee ownership checks,
source-of-truth validation, business outcome receipts or execution rights.
Suggested implementation types are declarations, not measured suitability.

Structural comparison finds one minimum activity insert/delete alignment.
Start/end/decision nodes route the graph without requiring source events for
those nodes. Alternative equal-cost alignments can exist. A prefix may be an
incomplete log rather than a business exception. Conditions on decision edges
are never evaluated from activity labels. Exact path matches do not verify
business rules, lawful processing, case completion or successful outcomes.

Statistics cover all cases; alignments show at most 80 operations per displayed
variant. Existing CSV input and rendering limits apply. Case identities remain
explicit CSV declarations, with no inferred cross-system or cross-person join.

The model is local reviewed knowledge and is visible to already authorized
knowledge readers. Save preserves the exact graph in the same append-only
owner namespace used by existing knowledge tools. Generic knowledge updates
must preserve a process graph rather than accidentally erase it. Gateway
knowledge records retain their existing per-actor boundary; this UI does not
introduce organization collaboration or publish local designs to the Gateway.

Delete explicitly removes all saved revisions for this local process. Downloaded
exports are independent copies. The catalog never persists native case IDs or raw event logs. Separate
[explicitly retained log snapshots](PROCESS_MINING_CSV.md#explicitly-retained-local-snapshots)
can preserve minimized structural case timelines under their own retention
controls; catalog Save does not retain an import. The model's labels, goals, declared rules and
explicitly supplied evidence references are persisted only on Save.

## API

All endpoints use existing local API/dashboard authentication and no-store
responses. No provider reads, model calls, agent runs or live business writes
occur here.

- `GET /v1/process-catalog?after=&limit=20`: latest process summaries in a
  cursor page of reviewed knowledge (a page can contain no processes).
- `POST /v1/process-catalog`: title, typed `process_model`, `user_confirmed`,
  optional `workflow_id`, `expected_revision`, and optional `evidence_refs`.
- `GET /v1/process-catalog/revision?workflow_id=...&revision=...`: exact version,
  or the latest when revision is omitted.
- `GET /v1/process-catalog/history?workflow_id=...&before_revision=...&limit=20`.
- `GET /v1/process-catalog/export?workflow_id=...&revision=...&format=json|bpmn|pdd`.
- `POST /v1/process-catalog/forget`: exact ID and `user_confirmed: true`.
- `POST /v1/process-mining/preview?workflow_id=...&revision=...`: bounded CSV
  preview plus structural comparison. Both parameters are required together.

Validation covers store reuse, owner isolation, revision conflicts, immutable
history, deletion, malformed graphs, branch/loop alignment, incomplete paths,
truncated display samples, HTTP auth/body limits and browser editing. A Chromium
acceptance pass covered UI → authenticated API → SQLite → reload → old-revision
export → CSV comparison using synthetic input. Cross-platform CI and real
customer rollout acceptance remain separate release gates.

## Improvement backlog

A reviewed revision can include an explicit opportunity scenario: currency,
monthly volume, minutes saved per eligible case, eligible fraction, loaded
hourly cost, implementation cost, recurring cost and chosen sensitivity range.
These assumptions are stored with the model revision. The backlog ranks by
projected net monthly value within one selected currency; it does not mix
currencies or derive labor savings from observed timestamps. Negative net
value has no positive payback estimate. Sensitivity is not a statistical
confidence interval. Unconfirmed-rule and unmapped-activity counts remain
visible beside the estimate.

`GET /v1/process-catalog/opportunities?currency=USD` scans at most 1,000 local
reviewed-knowledge records and returns at most 100 ranked scenarios, explicitly
marking either bound. Processes without assumptions are counted separately.
This is a planning backlog, not validated financial ROI or worker scoring.

## Runnable agent project

**Download OpenAI Agents project** exports an existing-runtime project pinned
to the saved process revision. `GET /v1/process-catalog/agent-project` accepts
exact `workflow_id` and `revision` query parameters and returns the ZIP.

The export also includes `configure.py`: explicit MCP metadata inspection and
model-assisted proposals for current tool names and argument mappings. Inspection
calls only protocol initialization and list-tools; proposal invokes an explicitly
chosen model with the process, minimized tool metadata and case field paths/types,
never the selected case's values. Tool descriptions and field names can themselves
be confidential, so the customer reviews the catalog before requesting a model
proposal. No tools are attached to that model; SDK tracing stays disabled.

Suggestions validate known eligible activity IDs, current tool names, real case
pointers and primitive argument types. Human-designated activities stay human.
The proposed bindings remain disabled, unauthorized and unmapped for actual
read/write execution; model recommendations are separate annotations. The customer
must review full current schemas, service semantics, result predicates, permission,
fresh preconditions, conditional writes, idempotency and outcome association.
Input-schema fingerprints from inspection are checked against current metadata
before execution; changed schemas stop calls pending review. These fingerprints
are content identifiers, not signed approval or proof of provider semantics.

The project contains an OpenAI Agents SDK 0.24 planner, structured response
schema, offline graph validator, bounded synthetic route examples, missing tool
binding declarations and executable unit tests. Process data stays in the
user message; no goal or rule text is inserted into privileged instructions or
Python code. SDK tracing export is disabled. The planner exposes no business
action tools. It returns clarification when business inputs are missing and
rejects proposed routes that reference another revision, leave the graph or
use unconfirmed branch rules. A model integrity digest also detects accidental
specification changes. Hashes are content identifiers, not signed attestations.

`python -m unittest discover -v` runs synthetic checks without provider access;
the actual SDK test uses an in-memory model and is skipped until requirements
are installed. Route enumeration samples at most eight routes, 80 nodes per
route and 2,000 expanded search states, reporting transition coverage and its
limits. It never claims full branch-condition or customer-case coverage.

Explicitly running `python agent.py --case authorized-case.json --model MODEL`
uses the customer's configured model provider and sends that chosen case and
process data. OWG never executes the downloaded project. The ZIP also contains a separate customer-run MCP implementation helper. It
starts in offline review mode with disabled bindings and authorization. An
operator must map current tools and JSON Pointer arguments, review result
predicates and provider-enforced concurrency/idempotency contracts, and approve
an expiring intent pinned to the exact specification, plan, case and bindings.
The model planner has no business tools and cannot approve its own intent.

Each write requires a fresh read precondition, an expected-version argument,
an idempotency contract and a confirmed terminal source expectation. The helper
checks the existing local OWG source verifier before tools; an unavailable
verifier or already-matching terminal state blocks writes. It records a minimized
exclusive journal before attempts, performs no automatic retries, rejects replay
of the same intent, and checks independent source state afterward. It stops on
uncertain effects or unexpected structured results. Projects exported directly from the organization catalog additionally require a
separate catalog read token and configured catalog URL. They check the exact
organization, model digest and latest active independent review before tool
actions, including after fresh write preconditions. A changed, archived,
unreviewed or unavailable design stops further calls. Local-only exports have no
organization origin and perform no catalog reads. Re-export and review after
process changes. The freshness check is not an atomic lock with a remote write
and cannot cancel in-flight actions.

Use the generated README for separate offline-review and explicitly approved
execution commands. Credentials are supplied separately through fixed environment
variables; neither credentials nor business cases are included in the export.
Intent files are local declarations, not signed approvals or target-service
permission. Customer service-side IAM, actual provider conditional-write and
idempotency semantics, authoritative case identity, and production acceptance
remain necessary. This helper supplies no deployment service, rollback or
distributed exactly-once guarantee. A source-state match does not establish agent
causality or business-goal completion. A structural plan or shadow-test pass does
not prove a live business result.

## Desktop and source-audit entry points

In the observed workflow explorer, select a map node in an exact displayed
variant and choose **Draft selected variant in process catalog**. A clipped
lane or core summary cannot be used. The draft preserves the displayed bounded
baseline and selected execution references. Candidate executions are explicitly
labeled as such; they are not verified business cases or task boundaries.

Selected Jira audit variants also offer **Draft this source variant**. Their
source coverage remains marked as bounded/partial; no cross-issue business-case
join or new source read is inferred. Both entry points create only an unsaved
draft. A business goal and exact graph review are required before Save.
Baseline labels remain untrusted data; unsupported proposed activity labels get
explicit Activity placeholders for the reviewer to replace.

## Source-state checks on a reviewed process

A revision can declare source-operation expectations for terminal activities.
Confirm the expected operation in the model and save it. Checking it requires
an exact observed `owg:r:` record, a separate source-read confirmation and a
live dashboard session. Existing source adapters enforce their own separately
enabled credentials, scope and retained-record identity boundaries. The source
check never enables a connection or performs business writes.

`POST /v1/process-catalog/check-outcome` accepts the exact workflow/revision,
terminal activity, declared provider operation, opaque record reference and
`source_reads_confirmed: true`. It reuses `source_operation_receipt` and returns
its freshness/identity/state checks alongside the pinned process reference.
Unsupported operations, missing review, nonexistent revisions, unconfirmed
expectations and non-dashboard callers are rejected before source reads.

A match verifies only the authenticated provider's operational state. It does
not show that this record really belongs to the business workflow, that an agent
caused the operation, that the whole process ran, or that the customer goal was
achieved. The association is explicitly user-selected. The receipt is not saved
as a new policy or process revision. No live connector or production business
outcome was certified by the synthetic acceptance tests.
