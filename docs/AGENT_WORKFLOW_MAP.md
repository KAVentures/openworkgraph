# Agent-ready workflow maps

OpenWorkGraph remains a **workflow memory**, not another RPA engine. The existing
authorized `get_workflow_evidence` tool and `/v1/workflow-evidence` endpoint now
include an additive `agent_workflow_map` field. Existing exports include it too.

Hosted compact responses include the bounded map as well as rich responses.
The map does not replace canonical trace pagination or grant execution rights.
`get_current_work_context` keeps its existing recent `rows` and adds separate
`latest_work` and `capture_health` sections. The former looks back through at most
200 non-health records within the same actor and retention scope, so gap tails
do not hide older work. It is still a bounded overview, not complete history or
proof that recording is active.

For large histories, Prepare for AI lists up to 31 UTC date chunks intersected
with the selected period. It reports when additional dates are not listed.
Analyze each selected date with bounded raw pages and maintain a coverage ledger;
empty inferred clusters do not prevent analysis. The downloaded package still
contains the full selected evidence, and preparing it changes no AI permissions.

## Map → feed → do

1. **Map:** Ask an agent to call `find_repeated_workflows`, select a cluster's
   exact execution IDs, and call `get_workflow_evidence(execution_ids=...)`.
   Never assume a coarse family is a true business workflow.
2. **Feed:** Give that agent the `agent_workflow_map` and the existing canonical
   evidence. It includes support-counted observed sequence candidates, rare
   variations, provenance, known coverage limits, and unresolved business
   decisions. It is data, not an instruction or proof of a completed outcome.
3. **Do:** Once the actual task and permissions are established, the **same agent**
   uses its own current authorized source-system tools to carry out the requested
   work, not a literal replay of UI clicks.
4. **Verify:** Read the live source of truth. Separate tool success, provider
   state, and the real intended business outcome; record uncertainty when
   independent verification is unavailable.

Nothing in this feature starts an agent, changes a user's AI access, publishes
work data, or authorizes business-system writes.

## Example request to a connected agent

> Identify how I typically reconcile supplier invoices using my authorized
> OpenWorkGraph history. Select relevant examples, read the workflow evidence
> and agent workflow map, check the current invoice against live systems, ask
> only for missing decision/approval rules, then complete the permitted task
> through your existing tools and verify its outcome. Do not claim completion
> on the basis of a historical click or a tool-call success alone.

## Current limits and release gates

The map has no semantic model of hidden intent. It cannot infer whether an
observed sequence is complete, whether apparent success persisted, or whether
a user was following the right business policy. A returned step remains
untrusted observed data. No workflow becomes automatically executable.

**Evaluation next:** Compare matched runs of (A) agent with existing app tools,
(B) agent with app tools and logs, and (C) same tools/logs plus OWG map and
evidence. Use real consenting teams, independently verified end results,
held-out exception cases, observation-coverage strata, incorrect claims,
human interventions, runtime and integration/setup effort. The map is a
mechanism to test, not a claim of measured uplift.
