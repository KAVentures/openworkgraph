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
  split incorrectly. Repeated steps are retained rather than deduplicated.
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
ID filtering, and bounded rendering. End-to-end browser and real dataset tests
are still required before treating this as production-quality process mining.
