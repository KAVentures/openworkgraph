# Goal-oriented work context (evidence-first, hosted MCP)

The hosted OpenWorkGraph connector exposes `get_goal_context(goal, max_examples=4,
since=None, until=None, detail="compact")`. It is a **read-only navigation tool**
for an AI trying to accomplish a user's goal with help from that user's
previously observed human or agent work.

## When to call

Use this tool when the current goal could materially benefit from past *observed
work*: “Build a better version of how I prepared quotations,” “We did this
last month; try again,” or “Can you improve how I handle this in my usual apps?”

It is **not** a substitute for normal reasoning or current source tools. Do not
call it for pure arithmetic, unrelated general factual questions or tasks with
no plausible work-history dependency. A connected AI client ultimately decides
whether to invoke the tool; MCP cannot force background operation or recall.

## What it provides

- Up to eight candidate *evidence neighborhoods*, sorted by deterministic
  overlap between the user's goal terms and observed application/window/resource
  surfaces.
- A compact set of actual neighboring events around each matching anchor.
  The underlying query-free trace remains available and authoritative.
- Bounded timestamps, resource pointers, an `omit_query` raw drilldown,
  coverage flags and an explicit warning that neither a likely match nor nearby
  activity proves a shared task, correct procedure or successful outcome.
- A raw `get_workflow_trace` fallback when matches are missing or the scan
  was bounded. `get_agent_runs` remains the dedicated agent-run context tool.

This is *not semantic search*. The first pass uses the latest 500 actor-scoped
synced events plus four bounded older-history lexical probes; promising hits
are expanded using query-free chronology. The absence of a hit is not evidence
that the user has never done that work. Older runs can be missed, and the local
recorder may contain richer evidence than the Gateway.

All records remain within the authenticated personal/organization actor
scope and current Gateway retention policy. The existing hosted redaction
projection and untrusted-evidence wrapper apply to all results, including
`detail="rich"`. No raw prompts, arbitrary metadata, clipboard contents or
unapproved local browser text are sent by this tool.

## Recommended agent workflow

1. The user supplies a goal. If past work is relevant, call
   `get_goal_context` with that goal (without asking the user to name OWG).
2. Inspect a small number of evidence neighborhoods and their observed
   applications/resources. Treat these as navigation hints, not instructions.
3. For relevant examples, call `get_workflow_trace` with the returned
   `since` and `until` and **no query**, and page when necessary.
4. Use `get_agent_runs` if explicit structural agent executions might help.
5. Use authorized live source-system connectors to inspect actual files,
   business rules, tool capabilities and current state before implementing.

## Scope and future work

This PR **does not** build an automation agent, a learned playbook store, an
approval system or an action policy engine. Execution autonomy and permissions
are the responsibilities of the user's AI/agent runtime and source systems,
not an OWG roadmap item.

Follow-ups to consider only after measurement: richer episode reconstruction,
reusable evidence-linked guides, explicit human-agent correction associations,
local/hosted tool parity, and a benchmark comparing goal retrieval against raw
chronology and no OWG. Do not treat successful MCP transport or tool catalog
initialization as evidence of automatic model invocation.

The full read-only history tools and capture/sync paths remain unchanged.


## AI conversation/run resource continuity (PR #197)

OpenWorkGraph already records structural ChatGPT/Claude Web agent lifecycle
signals (run started/finished and safe state) and separately opted-in native
Claude Code/Codex session observation. **Conversation contents, user prompts,
model responses, and code/terminal outputs are not required for this feature.**

With the existing **business-object/resource-reference capture permission**
enabled in the browser sensor, URL paths in the following specific shapes
produce resource references:

- `chatgpt.com/c/<UUID>` (also `chat.openai.com/c/<UUID>`)
- `claude.ai/chat/<UUID>`

The extension does not emit a whole browsing URL, fragments or queries as a
resource record. An installation-keyed opaque `owg:r:` reference is stored
with structured event evidence; the actual resolver locator remains
device-local and is never included in default Gateway data. Titles are
captured only by the separate ordinary browser-title mechanism and run through
device-side redaction before cloud upload. Conversation content is **not**
captured by the new reference parser. IDs are **not** proof of semantic
task identity and are not automatically resolved by the AI.

Host/goal hints are limited, lexical navigation suggestions, not new
authority. For example, `contract/avtal` can suggest `oneflow` and
`PR/pull request` can suggest `github` when those app hosts appear
in already-authorized synced evidence. A `no_relevant_match` result means no
candidate was found under current actor scope and bounded coverage, not proof
that the work did not happen.

The hosted repeated-workflow recommendation now suppresses clusters with
zero median duration or fewer than two common distinct surfaces. Those records
are still available from authorized canonical chronological evidence, and
the local discovery default remains unchanged for compatibility.

Default repeated-workflow MCP responses are compact navigation summaries;
pass `detail="rich"` for full structure before drafting a procedure.
The `total` and `returned` counters now both refer to candidate clusters,
while `families_total` and `families_returned` describe coarse families.

The live hosted connector can compute event freshness from the latest
synchronized observation but **cannot assert that the desktop recorder is
running** without a separately authenticated device heartbeat. Do not treat
stale evidence as conclusive proof of a crash or as a live monitoring signal.
