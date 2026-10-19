# Local AI case-context read

OpenWorkGraph preserves all original recorder, Activity, workflow mapping,
Discovery Mode, export, procedural-memory and AI-memory functionality.

The **additive** `GET /v1/case-context` endpoint and local MCP
`get_case_context(resource_ref)` give existing authorized agents a compact,
read-only case handoff for a previously observed `owg:r:` business resource.

The tool:
- validates an exact OWG resource pointer from already authorized evidence;
- calls the existing `record_graph` source and inherited retention/date bounds;
- includes a bounded structural chronology and the current provider status
  only if the caller specifically requests a refresh and the existing provider
  opt-in allows it;
- separates provider operational completion (e.g. Stripe invoice paid) from
  actual customer outcome, and preserves explicit unknowns;
- instructs agents to use existing `find_repeated_workflows` and
  `get_workflow_evidence` for workflow mapping, rather than inferring
  a prescribed process from one observed case;
- **never** grants permission to send messages, approve pull requests,
  issue refunds, make payments or modify any source system.

The new endpoint is covered by the same secure local app authentication.
The local MCP tool checks current AI-access grants before fetching evidence.
Crucially, `mcp_server/history_guard.py` now imposes **the exact same**
saved-history lease and current-run boundary as `/v1/record-graph`, preventing
a history-consent bypass. No extra uploaded content, cloud tool or external
OAuth connection is added. Existing MCP tools and names remain unchanged.

This known-case MCP tool is **different** from inventory discovery.
It does not enumerate unobserved open work. The experimental Stripe/GitHub
Open Work previews remain dashboard-session-only, explicitly confirmed reads.
Making their broad inventories available to agents requires separately scoped
and time-limited permissions; the existing general AI read switch is not
sufficient authorization for new provider-wide scans.

Real-agent usefulness and enterprise workflow uplift remain to be demonstrated
in a matched controlled evaluation (issue #247).

## Explicit experimental local MCP catalog opt-in

The stable default compact MCP catalog **does not change**. New Open Work
tools are registered only when the user's local MCP launcher process has
`OWG_OPEN_WORK_EXPERIMENTAL_TOOLS=1`. This is an experimental developer
switch, not a UI consent grant or authorization to access source inventories.

Even with the flag, every invocation still checks the user's regular
per-client AI access, the current history lease and the independent source
provider read setting. Turning the flag off restores the exact prior tool
catalog. Existing hosted/cloud MCP tools remain unchanged.
