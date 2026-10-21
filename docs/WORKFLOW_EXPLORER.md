# Visual workflow explorer

OpenWorkGraph offers **Explore observed workflows** on the local Overview page.
Its automatic projection reads the existing evidence API, with no second
collector or model call. Separate [reviewed grouping controls](WORKFLOW_GROUPINGS.md)
can name, split and combine selected classifications in the existing knowledge
ledger, while preserving inferred families and individual execution histories.

## Using the view

1. Install/run OWG normally and record consenting work.
2. Open Overview, select a period, and click **Explore workflows**.
3. Choose a candidate in the list to view its exact observed sequence variants.
4. Select a node to see its variant and step position. Use **Review selected
   examples** to request support-counted evidence for that variant, or
   **Copy AI handoff request** to ask an already connected AI to investigate it.
5. For an inferred candidate, **Explore combined candidate transitions** draws
   the normalized structural graph across all its projected executions, including
   variants omitted from the lane sample. Select a node by mouse or keyboard
   to inspect occurrence/execution counts, then explicitly select a displayed
   supporting variant before drafting its route. The combined map does not
   silently choose an execution or establish common business-case identity.

The map uses `/v1/workflow-evidence/families` and
`/v1/workflow-evidence` with their existing authenticated local context. No
storage, network destination, AI grants or recording permissions are introduced
by map inspection. Reviewed grouping saves are separate explicit writes to
user-reviewed knowledge; they do not modify canonical source evidence.

## Semantics and known limits

- The visualization draws **separate variant lanes**, avoiding invented merges
  or branches. Each lane is a candidate observed structural sequence, not
  an approved SOP, actual business task, or authorization.
- Workflow identification remains heuristic; interleaved cases may be merged or
  split incorrectly. The source projection collapses adjacent identical steps
  and clips at 48 steps; nonadjacent repeated steps remain visible. These are
  normalized structural steps, not canonical events.
- The optional combined graph counts projected occurrences and distinct
  supporting executions separately. It includes observed loops and alternate
  transitions without claiming that every path composed from those edges was
  ever observed. It provides no per-step timing, verified wait/labor, business
  rules or authorization. Up to 64 high-frequency step types and 4,096 transition
  types can be drawn; omitted types are stated separately while global
  projection totals remain intact.
- It displays at most 30 candidate groups, six variants per group, and 24 steps
  per variant. The UI shows when variants or steps are omitted.
- The evidence API considers at most 25,000 source event rows for this read.
  Its limit warning and requested time period are surfaced. Missing history
  is not proof that work did not happen.
- Titles and labels are drawn using DOM `textContent`, not HTML from event
  data. Canonical event drill-down stays in the existing Evidence/Export/MCP
  surfaces. A compact evidence inspection does not persist new information.
- Automatic family labels and boundaries remain heuristic. Load a reviewed
  group to inspect variants for its exact selected executions, or use the
  process catalog for proposed design/versioning, the CSV explorer for event-log
  maps and the organization catalog for collaboration and scenarios. None of
  these projections establishes an authoritative business-case join.

## Regression gates

The view must not change existing capture, repeated-workflow results, evidence
exports, Discovery Mode, MCP contracts, or permissions. Tests cover route/script
installation, read-only calls, step repetition, exact variant separation,
ID filtering, and bounded rendering. End-to-end browser checks include an owned synthetic dashboard-to-API flow, keyboard node inspection,
explicit variant selection, mobile layout and byte-for-byte canonical-row
preservation. Real customer datasets are still required for business validation.

The families endpoint accepts `include_transition_maps=true` for this view.
Its default stays false, preserving existing compact candidate/MCP payloads and
avoiding graph construction for callers that do not request it. The map uses
the same selected time window, authenticated evidence and source bounds.
