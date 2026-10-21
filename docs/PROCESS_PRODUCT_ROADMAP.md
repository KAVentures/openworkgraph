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
| Visual exploration | Observed workflow lanes (#263); CSV variant/activity/case inspection | Shared interactive analysis across evidence sources; reviewed grouping corrections |
| Task/case reconstruction | Exact-record interleaving lanes (#265) | Verified cross-system case identity, business boundaries and consenting cross-person handoffs |
| Process mining | Bounded local CSV (#266), single Jira audit (#270), selected Jira cases (#272) | Additional source adapters, coverage diagnostics and approved-model conformance |
| Opportunity ranking | Measured review signals and explicit ROI assumptions (#268) | Comparable process backlog, validated effort and tool suitability |
| Process design | Reviewed AS-IS / TO-BE draft and JSON export (#267) | Persistent revisions, ownership, rules, exceptions and collaboration |
| Implementation | Non-executing starter ZIP and structural BPMN (#269) | Working runtime/tool bindings, branch tests and sandbox execution |
| Testing and feedback | Existing agent traces and independent source receipts | Integrated workflow test suites, shadow evaluation and drift review |
| Company rollout | Existing Gateway, organization and deployment infrastructure | Cohesive process inventory, review roles and operational reporting |

The CSV drill-down connects diagram selections to exact variant statistics
and bounded supporting examples. It remains an ephemeral import; it does not
establish a persistent process catalog or authoritative business case graph.

## Next shared foundation

Build a versioned process model that references existing evidence and reviewed
workflow knowledge. Before adding a second model store, inspect the existing
procedural-memory and workflow-knowledge revision/export contracts. Preserve
the distinction between an observed path, a human proposal, confirmed rules,
and authorization to perform a particular business action.

A useful next acceptance scenario is: reopen a reviewed design, inspect its
baseline and evidence coverage, change one rule or activity, and save a new
revision without overwriting another reviewer's changes. Exports and agent
starters should identify the exact reviewed revision. Then attach conformance,
opportunity ranking and runtime generation to that same model.

Retain exact declared case identifiers for source joins; temporal adjacency
alone cannot establish workflow identity. Keep observation consent, evidence
access and execution permissions separate. Prefer existing authorized APIs
and agent runtimes over historical click replay.

See [workflow explorer](WORKFLOW_EXPLORER.md), [CSV mining](PROCESS_MINING_CSV.md),
[case lanes](RECORD_CASE_LANES.md), [designer](PROCESS_DESIGNER.md),
[opportunity review](PROCESS_OPPORTUNITY_REVIEW.md), [starter](AGENT_STARTER.md)
and [selected Jira cases](JIRA_SELECTED_CASES_PROCESS.md).
