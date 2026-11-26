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

A log with fewer than 5 cases (objects) answers no `aggregates`-level request: every figure would describe a
handful of identifiable cases. Share it at the `cases` level if agents should see it.

## MCP tools

These tools are listed once at least one log is shared. Connected apps see them after their next
restart. If every share is revoked, the tools stay listed but empty until that restart.

**Stop sharing every log** (in *Share with AI agents*, after a confirmation) withdraws every share and every tag permission at once, for all logs. Only the owner's dashboard can do this. Agents can neither stop nor start sharing.

| Tool | Answers |
| --- | --- |
| `list_process_logs` | Shared logs: id, name, level, size, case notions, whether agents may tag cases. |
| `get_process_overview` | Cases, events, durations, KPIs (optionally an SLA), top variants, top insights with their case filters, and completed cases per week for the last 52 weeks (median withheld under 5 cases). Optional `automation_resources` adds the share of events done by those declared bots or systems. |
| `get_process_overview_for_cases` | The overview of only the cases matching a case filter (same keys as insight cohorts, e.g. `{"attribute": "region=EU"}` or `{"activity": "Escalate"}`), next to the whole log: case count and share, durations, KPIs and top variants. Refused when fewer than 5 cases match. | aggregates |
| `get_process_variants` | The most common paths with cases, share and median duration, and how much of the log they cover, plus how many variants are needed to cover 50, 80, 90 and 95% of cases. Variants followed by fewer than 5 cases are withheld and counted. | aggregates |
| `get_process_map` | The process map: activities, which directly follows which (counts and gaps), start and end activities, with optional activity and path sliders. Activities and transitions in fewer than 5 cases are withheld. | aggregates |
| `get_process_duration_distribution` | Cases per duration band and the median, p90 and p95 duration (not the single shortest or longest case). | aggregates |
| `get_process_time_patterns` | Events and case arrivals by weekday and hour in a time zone, optionally for one activity, and the share outside working hours. | aggregates |
| `get_process_root_cause` | Associations with `duration_over` (hours), `rework`, `contains_activity` or `ends_with` (activity). |
| `compare_process_cohorts` | Two case filters side by side: case counts, duration difference with a bootstrap interval, rework and SLA rate differences with intervals, activities more common in one group, and variant shares. Each group needs 5 cases; rows where a group has 1 to 4 cases are withheld. | aggregates |
| `get_process_trace_clusters` | Groups of cases with similar behaviour: each cluster's cases, top variants, activities more or less common than in the whole log, and median throughput time. Clusters and variants with fewer than 5 cases are withheld. | aggregates |
| `check_process_compliance` | Rules you give (response, precedence, response within a time, not co-existence, segregation of duties, existence) checked against the cases: applicable and violating cases and the violation rate with a 95% interval. Violating case IDs only at the cases level; at the aggregates level a rule's counts are withheld when it applies to fewer than 5 cases or any count is 1 to 4. | aggregates |
| `get_process_performance_spectrum` | Time between two consecutive activities over the log's span, in fast/normal/slow/very slow classes per bin, with FIFO and batching shares. Segments seen fewer than 5 times are withheld; no individual case lines. | aggregates |
| `explain_process_case` | One case's steps, gaps over the typical gap, repeats and uncommon steps (cases level). |
| `get_slowest_process_cases` | Slowest cases first (cases level). |
| `get_process_signals` | Your signal rules and their last evaluated state: triggered or not, value or matching cases, and whether you reviewed it (aggregates level; case IDs at cases level). Never evaluates, acknowledges or snoozes. |
| `preview_process_signal` | Try a draft signal rule once (KPI, case, compliance, conformance or due-date condition) to propose a monitor: whether it would trigger now and on what value or how many cases. Nothing is saved; a filtered draft needs 5 cases; example case IDs only at cases level. | aggregates |
| `get_process_automation_candidates` | Activities ranked as automation candidates with every score component and routing pattern (aggregates level). See "Automation candidates" in EVENT_LOG_WORKSPACE.md. |
| `simulate_process_scenario` | What-if simulation calibrated from the log: baseline and scenario (arrivals, activity times, resources, paths, calendar, pools, priorities) with shared random numbers, cycle time, waiting and utilization for both, and optionally replications for uncertainty. At most 5,000 cases and 20 replications; calibrated paths are not returned. | aggregates |
| `get_process_decision_rules` | Without `activity`: the decision points and their branches. With `activity`: rules learned from data recorded at or before it, with held-out accuracy against the majority baseline (aggregates level). See "Decision rules" in EVENT_LOG_WORKSPACE.md. |
| `get_process_costs` | Process cost from the owner's saved cost model or prices the agent supplies: total, activity and holding cost, cost per case, cost per activity, repeated-execution cost and the costliest variants. No per-resource costs; activities and variants in fewer than 5 cases are withheld, and in agent-supplied prices such activities cost nothing. | aggregates |
| `get_process_temporal_profile` | For each ordered pair of activities, the mean and standard deviation of the time between them and how many occurrences deviate by more than `zeta` standard deviations, slower or faster (aggregates level, no case IDs). See "Temporal profile" in EVENT_LOG_WORKSPACE.md. |
| `get_process_intervention_impact` | Before/after impact of a change on cycle time, rework, SLA breach or an activity's count per case, with bootstrap intervals, an optional treated/control difference-in-differences and a placebo check. Periods with fewer than 10 cases show only their count. | aggregates |
| `get_process_heuristics_net` | The heuristics-miner model: arrows whose dependency is at or above `dependency_threshold`, splits and joins marked AND or XOR, and self- and two-step loops (aggregates level). See "Heuristics net" in EVENT_LOG_WORKSPACE.md. |
| `get_process_object_map` | The object-centric map for logs with several object types: per type its activities, which follows which for the same object, start and end activities; how often objects of two types share events; activities touching several types. Anything seen for fewer than 5 objects is withheld. | aggregates |
| `get_process_footprints` | Footprint matrices (→, ←, On logs with more activities than a model holds, the rarest are left out automatically and reported in `model_activities` (also for model quality, deviations and deviation causes). ||, #) from the recorded cases and from the discovered model, the share of cells that agree, and the orderings only the log has (with case counts) or only the model allows (aggregates level). See "Footprints" in EVENT_LOG_WORKSPACE.md. |
| `get_process_data_quality` | How far the log can be trusted: accepted and rejected rows by reason over all imports and appends, duplicates, shared timestamps, events without resource, future-dated events, single-event cases, attribute coverage, content integrity and the per-batch trend. | aggregates |
| `get_process_model_quality` | Fitness, precision, their F-score, generalization and simplicity of the discovered net at several noise levels (default 0, 0.1, 0.2, 0.3, 0.5), and the level with the best F-score, to choose a noise level before asking for the model (aggregates level). See "Model quality" in EVENT_LOG_WORKSPACE.md. |
| `get_process_model` | The discovered process model itself, to plan an automation or explain the process: steps, decisions, loops and parallel branches with their transitions, the observed share of cases per branch (unconfirmed rules), the model as a process tree in PM4Py notation, and how many cases it replays. Choose `noise` with `get_process_model_quality`; `activities` keeps the most frequent share. A reviewable draft that authorizes nothing (aggregates level, no case IDs or resources). |
| `get_process_deviations` | Optimal alignments of the cases on the discovered net at a noise level: alignment fitness, the steps cases took that the model does not allow there and the steps it requires that cases skipped (with case counts), and the step-by-step explanation of the most common non-fitting variants (aggregates level). See "Step-by-step explanation (alignments)" in EVENT_LOG_WORKSPACE.md. |
| `list_process_reference_nets` | The reference process models (Petri nets) the owner saved for the log, by id and name; the nets themselves are not returned. | aggregates |
| `get_process_reference_deviations` | Where cases depart from one saved reference model, from optimal alignments: extra and skipped steps with case counts, fitness overall and by month, and each non-fitting variant explained. | aggregates |
| `get_process_funnel` | Milestone funnel over 2 to 8 activities in order: cases reaching each step, conversion from the previous step, hours between steps (median, p90, mean), what stopped cases did last, and the largest drop; optional conversion window in hours (aggregates level). See "Milestone funnel" in EVENT_LOG_WORKSPACE.md. |
| `get_unusual_process_cases` | Cases that stand out, ranked, each with reasons (rare path, unusually slow or fast, unusually many events, heavy rework, rare activity) and the log baseline. Case IDs and paths only for logs shared at the cases level; aggregates-level logs get scores and reasons without them. See "Unusual cases" in EVENT_LOG_WORKSPACE.md. |
| `get_process_due_dates` | Whether an activity happened by each case's due date (from a case or event attribute, or N days after the case started): on time, early, late, open and overdue, open and not yet due, missing or unreadable due dates; days late and a trend by due month. Most-late case IDs only for logs shared at the cases level. See "Due dates" in EVENT_LOG_WORKSPACE.md. |
| `get_process_next_steps` | Path explorer: after a sequence of steps (empty = the case start; or anywhere in the cases), the next activities with case counts and shares, hours from the last step (median, p90) and to the case end, and how many cases end there. Call again with a step appended to go deeper (aggregates level). See "Path explorer" in EVENT_LOG_WORKSPACE.md. |
| `predict_open_process_cases` | Predictive monitoring: estimated remaining time of open cases (median, 10th to 90th percentile) and optionally an outcome risk (runs longer than N hours, or will contain an activity), with a temporal backtest. Per-case estimates and case IDs only at the cases level. | aggregates (summary), cases (per case) |
| `predict_next_process_activity` | The likely next activity of open cases from completed cases with the same recent activities, expected next activities across open cases, and a backtest. Per-case predictions only at the cases level. | aggregates (summary), cases (per case) |
| `get_process_insights` | Where to look first: ranked automatic findings (slow transitions, repeated activities, costly detours, the long tail, path spread, slowing down, SLA breaches with `sla_hours`), each with numbers, case share and the reproducing case filter (aggregates level). A good first call. See "Where to look first (automatic insights)" in EVENT_LOG_WORKSPACE.md. |
| `get_process_activity_glossary` | The owner's notes per activity: description, owning team, system and automation status (manual, partly, automated, candidate), with coverage. Activities seen in fewer than 5 distinct cases are left out. | aggregates |
| `get_process_deviation_causes` | Case attributes and activities associated with deviating from the discovered net (outcome: the case followed a path the optimal alignment found non-fitting), as risk differences with 95% intervals and a Bonferroni check (aggregates level). See "What goes with the deviations" in EVENT_LOG_WORKSPACE.md. |
| `align_process_case` | One case's optimal alignment on the discovered net: extra steps, skipped steps and fitness, step by step (cases level only). See "Explain a case". |
| `get_process_brief` | **Start here.** One bounded briefing: the overview (size, timing, KPIs, top paths, top insights), the discovered model at its best noise level (fitness, precision, F-score), the steps cases most often add or skip against it, unusual-case counts by reason, and suggested next tools. Parts that cannot be computed are listed in `parts_unavailable` (aggregates level). Includes the owner's latest change markers (label and date, never the note). |
| `get_process_model_change` | How the discovered model differs before and after a date (default: the median case start): changed footprint relations between activities, activities in only one period, and the cross-fit of each period's cases on the other period's model (aggregates level). See "Model change" in EVENT_LOG_WORKSPACE.md. |
| `compare_process_models` | The same comparison between two groups chosen by case attribute values ("region=EU" vs "region=US"): differing orderings, activities only one group has, and cross-fit of each group on the other's model (aggregates level). |
| `compare_process_logs` | Two shared logs side by side (regions, systems, before and after a migration): case counts, events per case, rework rate and durations, activities and paths only one log has, and the activities, paths and variants whose case share differs most, with 95% intervals. Optional case filters per side need at least 5 cases; rows where either log has 1 to 4 cases are withheld and counted. Both logs must be shared. | aggregates |
| `compare_process_cases` | Two cases side by side: aligned steps (shared or only in one), hours from each start, and where the second fell furthest behind (cases level only). |
| `get_process_activity_profile` | One activity from every side: share of cases, repeats, what comes before and after (with median gaps), time into the case, weekday and hour, and case durations with and without it (aggregates level). |
| `get_process_rework` | Rework: share of cases that repeat an activity, the activities repeated most, loop patterns (self-loop, short, longer), and how long cases with rework take compared with cases without (median difference with a 95% interval). | aggregates |
| `get_process_waiting_times` | Processing and waiting time per activity (timings resting on fewer than 5 instances are withheld) when the log records start/complete (lifecycle) or an end-time attribute; says so when it does not. Optional `working_calendar` (timezone, weekly hours, holidays) adds the same figures in working time. | aggregates |
| `get_process_drift` | Concept drift: cases grouped by start week or month, adjacent windows compared on their paths with a permutation test, and for each change point the paths whose share moved most. | aggregates |
| `get_stuck_process_cases` | Open cases idle much longer than usual after their latest activity, with how many times the usual wait they exceed. | cases |
| `get_process_signal_scorecard` | The owner's KPI, conformance and due-date signals month by month (by case start), with each month's value and whether the condition held; read-only (aggregates level). |
| `get_process_attributes` | Which case and event attributes the log records, to choose filters, breakdowns and root-cause features: coverage, numeric or categorical, case- or event-level, quartiles, and the most frequent values with their case share and median duration. Values in fewer than 5 cases, minimum and maximum, and values of identifier-like attributes are withheld. | aggregates |
| `get_process_breakdown` | Case figures by one or two dimensions (start month or weekday, first or last activity, a case or event attribute), like a pivot table: cases, share, median and p90 duration, events per case, rework rate and optionally the share doing an activity. Cells and totals with fewer than 5 cases are withheld. | aggregates |
| `find_similar_process_cases` | The recorded cases whose path is closest to a chosen case, with what each does more or less often, its duration and start, and the median duration of those found. | cases |
| `get_process_cooccurrence` | Which activities happen in the same cases, regardless of order: cases with both, the share of cases with one that also have the other, lift, and the pairs that go together most or never. Activities in fewer than 5 cases and pair counts below 5 are withheld. | aggregates |
| `get_process_work_in_progress` | Cases arriving, completing and open per day or week, optionally with the activities that complete a case, and a Little's law check. Optional `case_filter` narrows the cases, for example when stray dates stretch the log past 400 buckets. | aggregates |
| `get_process_batching` | Batch processing per activity: share of events in batches, batch sizes, simultaneous or sequential batches, and the wait before batched events. Resources are counted, never named. | aggregates |
| `get_process_handovers` | Handover of work, subcontracting (ping-pong) and working together between pseudonymous resources, and who does which activity. Never names people; pairs or resources in fewer than 5 cases are withheld; no per-person durations or scoring. Optional `case_filter` limits it to one group of at least 5 cases. | aggregates |
| `get_process_roles` | Roles mined from which activities resources perform: member count, activity mix and activities only that role performs. Members are never listed; roles or activities under 5 events are left out. Optional `case_filter` limits it to one group of at least 5 cases. | aggregates |

| `tag_process_cases` | Adds an `agent: ` tag with a note to cases (cases level and the tag permission). |

All tools except `tag_process_cases` are read-only. All are idempotent. Their answers repeat that
durations are elapsed time between recorded events, that associations are not causes, and that
nothing authorizes an action.

### Prompt: `investigate_process`

When a log is shared, the MCP server also offers the prompt `investigate_process(question, log_id)`. It gives the
connected AI a step-by-step playbook: overview, data quality, variants, where time goes, why cases differ, change
over time, then options and simulation. It sets the rules for reporting: associations rather than causes, withheld
counts acknowledged, people never named, simulations as model outputs. It is plain text; every tool keeps its own
sharing checks.

### Prompt: `propose_process_monitors`

`propose_process_monitors(goal, log_id)` asks the AI to propose up to five monitoring signals for the owner. It checks
the existing signals, looks at trends, drift and stuck or overdue cases, and tests each draft rule with
`preview_process_signal` before proposing it. Nothing is saved; the owner decides which to keep.

### Prompt: `evaluate_process_change`

`evaluate_process_change(change, log_id)` asks the AI to weigh a proposed change: baseline, an observed comparison
with cases that already work that way, 2 to 4 simulated alternatives side by side, side effects, and what to
monitor afterwards. Observed and simulated evidence are kept apart; nothing authorises making the change.

## HTTP

| Route | Who |
| --- | --- |
| `GET/POST /v1/event-logs/{log_id}/agent-share` (`{"level": "aggregates" \| "cases" \| null}`) | Dashboard session only. |
| `POST /v1/process-intelligence/stop-sharing` | Dashboard session only: stops sharing every log and removes every tag permission; returns the names of the logs that were shared. |
| `GET /v1/process-intelligence/logs` | Local AI bearer (with AI access on) or dashboard. |
| `POST /v1/process-intelligence/{log_id}/impact` (`{"intervention_at", "metric"?, "activity"?, "sla_hours"?, "window_days"?, "exclude_recent_days"?, "exclude_spanning"?, "treated"?, "control"?, "case_type"?}`) | Agent, `aggregates` or `cases`. |
| `GET /v1/process-intelligence/{log_id}/overview?case_type=&sla_hours=` | Same, `aggregates` or `cases`. |
| `GET /v1/process-intelligence/{log_id}/root-cause?outcome=&hours=&activity=&case_type=` | Same, `aggregates` or `cases`. |
| `POST /v1/process-intelligence/{log_id}/costs` (`{"model"?, "case_type"?, "limit"?}`) | Same, `aggregates` or `cases`; without `model`, the owner's saved cost model (422 when none). |
| `GET /v1/process-intelligence/{log_id}/case?case_id=&case_type=` | Same, `cases` only. |
| `POST /v1/process-intelligence/{log_id}/simulate` (`{"scenario": {...}, "case_type"?}`) | Agent, `aggregates` or `cases`. |
| `GET /v1/process-intelligence/{log_id}/slowest-cases?limit=&case_type=` | Same, `cases` only. |
| `GET /v1/process-intelligence/{log_id}/data-quality` | Same, `aggregates` or `cases`. |
| `GET /v1/process-intelligence/{log_id}/signals` | Same, `aggregates` or `cases` (case IDs only at `cases`). |
| `GET /v1/process-intelligence/{log_id}/automation-candidates?limit=&case_type=` | Same, `aggregates` or `cases`. |
| `GET /v1/process-intelligence/{log_id}/activity-glossary` | Same, `aggregates` or `cases`. |
| `GET /v1/process-intelligence/{log_id}/decision-rules?activity=&max_depth=&case_type=` | Same, `aggregates` or `cases`. |
| `GET /v1/process-intelligence/{log_id}/open-case-predictions?end_activity=&outcome=&outcome_hours=&outcome_activity=&limit=&case_type=` | Same, `aggregates` or `cases` (per-case estimates at `cases`). |
| `GET /v1/process-intelligence/{log_id}/open-case-next-activity?end_activity=&history=&limit=&case_type=` | Same, `aggregates` or `cases` (per-case predictions at `cases`). |
| `GET /v1/process-intelligence/{log_id}/temporal-profile?zeta=&case_type=` | Same, `aggregates` or `cases`. |
| `GET /v1/process-intelligence/{log_id}/object-map` | Same, `aggregates` or `cases`. |
| `GET /v1/process-intelligence/{log_id}/heuristics-net?dependency_threshold=&case_type=` | Same, `aggregates` or `cases`. |
| `GET /v1/process-intelligence/{log_id}/trace-clusters?k=&max_k=&case_type=` | Same, `aggregates` or `cases`. |
| `POST /v1/process-intelligence/{log_id}/compliance` (`{"rules": [...], "case_type"?}`) | Same, `aggregates` or `cases` (violating case IDs at `cases`). |
| `GET /v1/process-intelligence/{log_id}/performance-spectrum?segment=A->B&top=&bins=&case_type=` | Same, `aggregates` or `cases`. |
| `GET /v1/process-intelligence/{log_id}/footprints?noise=&case_type=` | Same, `aggregates` or `cases`. |
| `GET /v1/process-intelligence/{log_id}/variants?limit=&case_type=` | Same, `aggregates` or `cases`. |
| `GET /v1/process-intelligence/{log_id}/process-map?activities=&paths=&case_type=` | Same, `aggregates` or `cases`. |
| `GET /v1/process-intelligence/{log_id}/duration-distribution?cutoffs_seconds=&case_type=` | Same, `aggregates` or `cases`. |
| `GET /v1/process-intelligence/{log_id}/time-patterns?time_zone=&activity=&case_type=` | Same, `aggregates` or `cases`. |
| `POST /v1/event-logs/{log_id}/agent-share/tags` (`{"allowed": true \| false}`) | Dashboard session only. |
| `GET /v1/event-logs/{log_id}/agent-access?limit=` | Dashboard session only: the latest agent calls that reached this log's tools (time, tool, outcome: ok, not_shared, not_allowed, invalid or error), with counts per tool over 30 days. Recorded automatically; requests rejected before any tool runs are not, and no parameters or answers are kept. |
| `POST /v1/process-intelligence/{log_id}/case-tags` (`{"tag", "case_ids", "note"?, "case_type"?}`) | Agent, `cases` level and the tag permission (403 without it). |
| `GET /v1/process-intelligence/{log_id}/co-occurrence?limit=&min_support=&case_type=` | Agent, `aggregates` or `cases` (`min_support` at least 5). |
| `GET /v1/process-intelligence/{log_id}/work-in-progress?bucket=day\|week&end_activity=&case_type=` | Agent, `aggregates` or `cases`. |
| `GET /v1/process-intelligence/{log_id}/batching?gap_seconds=&min_batch_size=&by_resource=&case_type=` | Agent, `aggregates` or `cases`. |
| `GET /v1/process-intelligence/{log_id}/handovers?limit=&case_type=` | Agent, `aggregates` or `cases`. |
| `GET /v1/process-intelligence/{log_id}/roles?threshold=&case_type=` | Agent, `aggregates` or `cases`. |

Not covered: acting in source systems. That would need separate, explicit action authority,
which this feature never grants.
