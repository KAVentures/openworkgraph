# Process discovery to verified automation

OWG's target is one connected experience: task mining, process mining,
workflow mapping, handoff to existing agents, and independent outcome
verification. Existing evidence, record graphs, procedural memory, source
adapters and agent traces remain the foundation.

## Current implementation

This inventory reflects repository capabilities, not runtime certification
or full competitor parity.

| Area | Available increment | Main remaining gap |
| --- | --- | --- |
| Visual exploration | Observed workflow lanes (#263), CSV activity/case inspection, desktop/Jira-to-catalog drafting | Shared interactive analysis across evidence sources; reviewed grouping corrections |
| Task/case reconstruction | Exact-record interleaving lanes (#265) | Verified cross-system case identity, business boundaries and consenting cross-person handoffs |
| Process mining | Bounded local CSV (#266), single Jira audit (#270), selected Jira cases (#272), full-log structural conformance | Additional source adapters, coverage diagnostics and validated business-rule conformance |
| Opportunity ranking | Measured signals (#268), revisioned assumptions and currency-scoped improvement backlog | Validated employee effort, business outcomes and tool suitability |
| Process design | Reviewed draft (#267), versioned graph catalog, direct shared graph editing, declared rules, renderable BPMN/PDD/spec exports, authenticated shared ownership and independent revision review | Real-time collaboration, richer rule clarification and business-policy approval lifecycle |
| Implementation | Starter ZIP (#269), runnable OpenAI Agents SDK planner and synthetic tests pinned to a saved revision | Authorized live tool bindings, deployment and end-to-end business tests |
| Testing and feedback | Agent traces/receipts, full-log structural comparison, workflow shadow tests, pinned terminal source-state checks | Live case evaluation, verified business outcomes and drift review |
| Company rollout | Existing Gateway, organization/deployment infrastructure, explicitly shared process inventory, scoped editor/reviewer roles, revision history and audit | Integrated employee SSO for process portal, deployment controls and operational reporting |

The CSV drill-down connects diagram selections to exact variant statistics
and bounded supporting examples. CSV analysis itself remains ephemeral; only explicitly reviewed designs are
saved to the catalog. Neither establishes authoritative cross-system case identity.

## Shared foundation and next acceptance scenario

The [versioned process catalog](PROCESS_CATALOG.md) now extends existing
owner-scoped workflow knowledge. Designs retain an observed baseline, explicit
activities/transitions, declared rules and proposed implementation types.
Saving creates immutable revisions with optimistic conflict checks. Exports,
structural comparisons and agent projects identify the exact reviewed version.
No second evidence collector or model database was introduced.

The [organization catalog](PROCESS_COLLABORATION.md) publishes only explicitly
reviewed designs into a separate namespace in that same store. Authenticated
authors, exact-revision independent reviews, ownership and collaborator changes
are committed with minimal audits on SQLite and PostgreSQL. A new revision
requires a new review. Organization design reviews grant no business execution
authority; local evidence remains separate.

The next acceptance scenario requires an authorized real workflow: map tools
and case identity for one reviewed process, run its generated planner in a
customer-controlled sandbox, validate normal/exception cases against confirmed
business rules, and verify resulting operations with independent source
receipts. Extend that same model with production feedback and reviewer-approved
updates. Structural tests and synthetic data cannot certify that lifecycle.

Retain exact declared case identifiers for source joins; temporal adjacency
alone cannot establish workflow identity. Keep observation consent, evidence
access and execution permissions separate. Prefer existing authorized APIs
and agent runtimes over historical click replay.

See [workflow explorer](WORKFLOW_EXPLORER.md), [CSV mining](PROCESS_MINING_CSV.md),
[case lanes](RECORD_CASE_LANES.md), [designer](PROCESS_DESIGNER.md),
[opportunity review](PROCESS_OPPORTUNITY_REVIEW.md), [starter](AGENT_STARTER.md)
and [selected Jira cases](JIRA_SELECTED_CASES_PROCESS.md).
