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
| `aggregates` | Overview (cases, durations, KPIs, top variants), automatic insights, root-cause associations and automation candidates. |
| `cases` | The above, plus explaining one case and listing the slowest cases. |

Sharing a log needs a dashboard confirmation; stopping does not. Every agent request also needs
the global AI-access switch, and every response passes through the existing AI-context redaction
at your chosen detail level. At the default level, case IDs, resources and other identifiers
reach the agent pseudonymised. Choose full detail if the agent should work with real case IDs.

Sharing grants reading only. An agent cannot import, append, change or delete anything through
it, and the dashboard-only event-log API stays closed to agents. Revoking a share, or deleting
the log, takes effect on the next request.

### Letting agents record decisions as case tags

Agents can record a decision about cases, for example "needs review" or "duplicate invoice".
Celonis also offers this kind of write-back. Here it is opt-in and narrow:

- It needs the `cases` level **and** a separate checkbox, "Let AI agents tag cases". That also
  needs a confirmation, and it is off again whenever the level drops below `cases`.
- An agent can only add case tags. Every tag starts with `agent: ` and its note starts with
  "Added by an AI agent." You see these tags under Case tags, can filter any analysis with them,
  and can remove them like your own.
- At the redacted detail level the agent passes back the pseudonymous case IDs it was shown.
  OpenWorkGraph matches them locally by redacting the log's own IDs the same way. It refuses
  a pseudonym that matches more than one case and counts it as `ambiguous_case_ids`. It does
  not accept real IDs from the agent, because the agent never saw them.
- A tag changes nothing else and authorizes no action in any source system.

## MCP tools

These tools are listed once at least one log is shared. Connected apps see them after their next
restart. If every share is revoked, the tools stay listed but empty until that restart.

| Tool | Answers |
| --- | --- |
| `list_process_logs` | Shared logs: id, name, level, size, case notions, whether agents may tag cases. |
| `get_process_overview` | Cases, events, durations, KPIs (optionally an SLA), top variants, top insights with their case filters. |
| `get_process_root_cause` | Associations with `duration_over` (hours), `rework`, `contains_activity` or `ends_with` (activity). |
| `explain_process_case` | One case's steps, gaps over the typical gap, repeats and uncommon steps (cases level). |
| `get_slowest_process_cases` | Slowest cases first (cases level). |
| `get_process_signals` | Your signal rules and their last evaluated state: triggered or not, value or matching cases, and whether you reviewed it (aggregates level; case IDs at cases level). Never evaluates, acknowledges or snoozes. |
| `get_process_automation_candidates` | Activities ranked as automation candidates with every score component and routing pattern (aggregates level). See "Automation candidates" in EVENT_LOG_WORKSPACE.md. |
| `get_process_decision_rules` | Without `activity`: the decision points and their branches. With `activity`: rules learned from data recorded at or before it, with held-out accuracy against the majority baseline (aggregates level). See "Decision rules" in EVENT_LOG_WORKSPACE.md. |
| `get_process_temporal_profile` | For each ordered pair of activities, the mean and standard deviation of the time between them and how many occurrences deviate by more than `zeta` standard deviations, slower or faster (aggregates level, no case IDs). See "Temporal profile" in EVENT_LOG_WORKSPACE.md. |
| `get_process_heuristics_net` | The heuristics-miner model: arrows whose dependency is at or above `dependency_threshold`, splits and joins marked AND or XOR, and self- and two-step loops (aggregates level). See "Heuristics net" in EVENT_LOG_WORKSPACE.md. |
| `get_process_footprints` | Footprint matrices (→, ←, ||, #) from the recorded cases and from the discovered model, the share of cells that agree, and the orderings only the log has (with case counts) or only the model allows (aggregates level). See "Footprints" in EVENT_LOG_WORKSPACE.md. |
| `get_process_model_quality` | Fitness, precision, their F-score, generalization and simplicity of the discovered net at several noise levels (default 0, 0.1, 0.2, 0.3, 0.5), and the level with the best F-score, to choose a noise level before asking for the model (aggregates level). See "Model quality" in EVENT_LOG_WORKSPACE.md. |
| `get_process_deviations` | Optimal alignments of the cases on the discovered net at a noise level: alignment fitness, the steps cases took that the model does not allow there and the steps it requires that cases skipped (with case counts), and the step-by-step explanation of the most common non-fitting variants (aggregates level). See "Step-by-step explanation (alignments)" in EVENT_LOG_WORKSPACE.md. |
| `get_process_funnel` | Milestone funnel over 2 to 8 activities in order: cases reaching each step, conversion from the previous step, hours between steps (median, p90, mean), what stopped cases did last, and the largest drop; optional conversion window in hours (aggregates level). See "Milestone funnel" in EVENT_LOG_WORKSPACE.md. |
| `get_unusual_process_cases` | Cases that stand out, ranked, each with reasons (rare path, unusually slow or fast, unusually many events, heavy rework, rare activity) and the log baseline. Case IDs and paths only for logs shared at the cases level; aggregates-level logs get scores and reasons without them. See "Unusual cases" in EVENT_LOG_WORKSPACE.md. |
| `get_process_due_dates` | Whether an activity happened by each case's due date (from a case or event attribute, or N days after the case started): on time, early, late, open and overdue, open and not yet due, missing or unreadable due dates; days late and a trend by due month. Most-late case IDs only for logs shared at the cases level. See "Due dates" in EVENT_LOG_WORKSPACE.md. |
| `get_process_next_steps` | Path explorer: after a sequence of steps (empty = the case start; or anywhere in the cases), the next activities with case counts and shares, hours from the last step (median, p90) and to the case end, and how many cases end there. Call again with a step appended to go deeper (aggregates level). See "Path explorer" in EVENT_LOG_WORKSPACE.md. |
| `get_process_insights` | Where to look first: ranked automatic findings (slow transitions, repeated activities, costly detours, the long tail, path spread, slowing down, SLA breaches with `sla_hours`), each with numbers, case share and the reproducing case filter (aggregates level). A good first call. See "Where to look first (automatic insights)" in EVENT_LOG_WORKSPACE.md. |
| `get_process_deviation_causes` | Case attributes and activities associated with deviating from the discovered net (outcome: the case followed a path the optimal alignment found non-fitting), as risk differences with 95% intervals and a Bonferroni check (aggregates level). See "What goes with the deviations" in EVENT_LOG_WORKSPACE.md. |
| `align_process_case` | One case's optimal alignment on the discovered net: extra steps, skipped steps and fitness, step by step (cases level only). See "Explain a case". |
| `get_process_brief` | **Start here.** One bounded briefing: the overview (size, timing, KPIs, top paths, top insights), the discovered model at its best noise level (fitness, precision, F-score), the steps cases most often add or skip against it, unusual-case counts by reason, and suggested next tools. Parts that cannot be computed are listed in `parts_unavailable` (aggregates level). |
| `get_process_model_change` | How the discovered model differs before and after a date (default: the median case start): changed footprint relations between activities, activities in only one period, and the cross-fit of each period's cases on the other period's model (aggregates level). See "Model change" in EVENT_LOG_WORKSPACE.md. |
| `compare_process_models` | The same comparison between two groups chosen by case attribute values ("region=EU" vs "region=US"): differing orderings, activities only one group has, and cross-fit of each group on the other's model (aggregates level). |
| `compare_process_cases` | Two cases side by side: aligned steps (shared or only in one), hours from each start, and where the second fell furthest behind (cases level only). |
| `get_process_activity_profile` | One activity from every side: share of cases, repeats, what comes before and after (with median gaps), time into the case, weekday and hour, and case durations with and without it (aggregates level). |
| `get_process_signal_scorecard` | The owner's KPI, conformance and due-date signals month by month (by case start), with each month's value and whether the condition held; read-only (aggregates level). |
| `get_process_breakdown` | Case figures by one or two dimensions (start month or weekday, first or last activity, a case or event attribute), like a pivot table: cases, share, median and p90 duration, events per case, rework rate and optionally the share doing an activity. Cells and totals with fewer than 5 cases are withheld. | aggregates |
| `find_similar_process_cases` | The recorded cases whose path is closest to a chosen case, with what each does more or less often, its duration and start, and the median duration of those found. | cases |

| `tag_process_cases` | Adds an `agent: ` tag with a note to cases (cases level and the tag permission). |

All tools except `tag_process_cases` are read-only. All are idempotent. Their answers repeat that
durations are elapsed time between recorded events, that associations are not causes, and that
nothing authorizes an action.

## HTTP

| Route | Who |
| --- | --- |
| `GET/POST /v1/event-logs/{log_id}/agent-share` (`{"level": "aggregates" \| "cases" \| null}`) | Dashboard session only. |
| `GET /v1/process-intelligence/logs` | Local AI bearer (with AI access on) or dashboard. |
| `GET /v1/process-intelligence/{log_id}/overview?case_type=&sla_hours=` | Same, `aggregates` or `cases`. |
| `GET /v1/process-intelligence/{log_id}/root-cause?outcome=&hours=&activity=&case_type=` | Same, `aggregates` or `cases`. |
| `GET /v1/process-intelligence/{log_id}/case?case_id=&case_type=` | Same, `cases` only. |
| `GET /v1/process-intelligence/{log_id}/slowest-cases?limit=&case_type=` | Same, `cases` only. |
| `GET /v1/process-intelligence/{log_id}/signals` | Same, `aggregates` or `cases` (case IDs only at `cases`). |
| `GET /v1/process-intelligence/{log_id}/automation-candidates?limit=&case_type=` | Same, `aggregates` or `cases`. |
| `GET /v1/process-intelligence/{log_id}/decision-rules?activity=&max_depth=&case_type=` | Same, `aggregates` or `cases`. |
| `GET /v1/process-intelligence/{log_id}/temporal-profile?zeta=&case_type=` | Same, `aggregates` or `cases`. |
| `GET /v1/process-intelligence/{log_id}/heuristics-net?dependency_threshold=&case_type=` | Same, `aggregates` or `cases`. |
| `GET /v1/process-intelligence/{log_id}/footprints?noise=&case_type=` | Same, `aggregates` or `cases`. |
| `POST /v1/event-logs/{log_id}/agent-share/tags` (`{"allowed": true \| false}`) | Dashboard session only. |
| `POST /v1/process-intelligence/{log_id}/case-tags` (`{"tag", "case_ids", "note"?, "case_type"?}`) | Agent, `cases` level and the tag permission (403 without it). |

Not covered: acting in source systems. That would need separate, explicit action authority,
which this feature never grants.
