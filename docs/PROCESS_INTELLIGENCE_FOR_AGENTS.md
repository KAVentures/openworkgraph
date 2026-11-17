# Process intelligence for AI agents

Process-mining vendors now hand their process context to AI agents. Celonis ships an Agent
Tools (MCP) asset for agents in Copilot Studio, Bedrock or Agentforce. SAP Signavio has Joule,
which answers process questions in natural language. Mimica has a conversational "Analyst".
OpenWorkGraph's event-log workspace answers the same questions locally: performance, variants,
insights, root cause and why a case is slow. This feature lets an AI app you connect to
OpenWorkGraph (Claude, a local agent) ask those questions over MCP, **only for logs you choose
to share**.

## What the owner controls

Imported business logs stay closed to AI tokens and MCP clients by default. In the dashboard,
"Share with AI agents" sets one log at a time to one of these levels:

| Level | The agent may read |
| --- | --- |
| Not shared (default) | Nothing; the log answers exactly like an unknown log, so it cannot be discovered. |
| `aggregates` | Overview (cases, durations, KPIs, top variants), automatic insights and root-cause associations. |
| `cases` | The above, plus explaining one case and listing the slowest cases. |

Sharing a log needs a dashboard confirmation; stopping does not. Every agent request also needs
the global AI-access switch, and every response passes through the existing AI-context redaction
at your chosen detail level. At the default level, case IDs, resources and other identifiers
reach the agent pseudonymised. Choose full detail if the agent should work with real case IDs.

Sharing grants reading only. An agent cannot import, append, change, tag or delete anything
through it, and the dashboard-only event-log API stays closed to agents. Revoking a share, or
deleting the log, takes effect on the next request.

## MCP tools

These tools are listed once at least one log is shared. Connected apps see them after their next
restart. If every share is revoked, the tools stay listed but empty until that restart.

| Tool | Answers |
| --- | --- |
| `list_process_logs` | Shared logs: id, name, level, size, case notions. |
| `get_process_overview` | Cases, events, durations, KPIs (optionally an SLA), top variants, top insights with their case filters. |
| `get_process_root_cause` | Associations with `duration_over` (hours), `rework`, `contains_activity` or `ends_with` (activity). |
| `explain_process_case` | One case's steps, gaps over the typical gap, repeats and uncommon steps (cases level). |
| `get_slowest_process_cases` | Slowest cases first (cases level). |

All tools are read-only and idempotent. Their answers repeat that durations are elapsed time
between recorded events, that associations are not causes, and that nothing authorizes an action.

## HTTP

| Route | Who |
| --- | --- |
| `GET/POST /v1/event-logs/{log_id}/agent-share` (`{"level": "aggregates" \| "cases" \| null}`) | Dashboard session only. |
| `GET /v1/process-intelligence/logs` | Local AI bearer (with AI access on) or dashboard. |
| `GET /v1/process-intelligence/{log_id}/overview?case_type=&sla_hours=` | Same, `aggregates` or `cases`. |
| `GET /v1/process-intelligence/{log_id}/root-cause?outcome=&hours=&activity=&case_type=` | Same, `aggregates` or `cases`. |
| `GET /v1/process-intelligence/{log_id}/case?case_id=&case_type=` | Same, `cases` only. |
| `GET /v1/process-intelligence/{log_id}/slowest-cases?limit=&case_type=` | Same, `cases` only. |

Not covered yet: writing decisions back, and acting in source systems. Those would need
separate, explicit action authority, which this feature never grants.
