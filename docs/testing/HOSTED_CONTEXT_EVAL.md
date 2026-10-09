# Hosted OpenWorkGraph multi-client retrieval evaluation

This is an **evaluation protocol and scorer**, not a claim that any ChatGPT,
Claude, Codex, or other AI has actually retrieved a user's OWG evidence. It
measures *when* agents use MCP, whether the right tools were invoked, whether
raw chronology was fetched when inference failed, and which expected synthetic
event IDs were retrieved.

## Production readiness snapshot (2026-10-09)

At inspection, Vercel's production plugin deployment of commit `22b83cd`
was READY with the hosted redaction and compact-response code. PR #175 CI
passed. Domains were verified in Vercel. OWG Supabase Gateway aggregates:
**one issued device-link grant, zero used grants, zero evidence events, zero
successful plugin context reads**. These are point-in-time observations,
not a guaranteed future status.

The remote plugin is a **private personal plugin (v0.1.2)**. It does not
appear as a callable tool in every ChatGPT session. A private plugin's
installation is distinct from device enrollment and syncing evidence.

Production end-to-end smoke test (requires the user, and an OS app install):
1. In OWG desktop, open **Connect AI → Connect ChatGPT**, use the same
   OWG/Supabase identity as ChatGPT, and explicitly approve **Link and share
   new evidence**. The personal link is a one-time short-lived grant; if it
   was issued before the Gateway certificate existed, restart the link flow.
   Keep agent content sharing **off** for the initial test.
2. Perform a few harmless synthetic work actions after sharing begins.
   Do not upload a previously captured private history to "make testing
   easier". Capture only intentionally testable activity.
3. In ChatGPT web, connect/enable the OWG plugin with OAuth. Request
   "Use my OpenWorkGraph evidence to identify the last actions I performed."
4. Confirm the authenticated MCP call succeeded with 15 compact default
   rows, correct timestamps and stable event IDs. Use a narrow, date-bounded
   `get_workflow_trace(detail="rich")` to verify surrounding event sequence,
   and verify redaction still holds. Never share access tokens or HTTP logs
   containing credentials.
5. Inspect **aggregate** Gateway statistics for link consumed, events
   ingested and `plugin.context.read` audit. Distinguish Gateway evidence
   synchronization from plugin OAuth logins: both are necessary.

If sign-in or access fails, record the failing step and HTTP status, but not
tokens or private event payloads. A correct unauthenticated `401` is
*not* evidence of authenticated end-to-end success.

## Model-in-the-loop benchmark

The source of truth is `evals/hosted_context/cases.json` (22 synthetic
intent scenarios, including six negative controls) together with
`evals/hosted_context/fixtures.py` (a deterministic, PII-free synthetic
chronology with an older relevant event, more than 200 later distractions,
interleaved unrelated records and repeated-work examples).
Generate fixture events with:

```bash
python -m evals.hosted_context.fixtures --out /tmp/owg-synthetic-evidence.jsonl
```

Load those events into a **disposable, isolated Gateway** test tenant, through
its normal authenticated enrollment/ingestion path. Do NOT seed the actual
production customer tenant, and do NOT bypass Gateway organization policy
to share agent runs. If an agent run is needed in the test, enable structural
agent sharing only for an explicitly consenting **test** identity and turn
it back off after the test. An empty-Gateway scenario needs a separate test
tenant with no events.
The model prompts must be
presented **without naming the expected tools**. Test at least two distinct
MCP-capable clients/models and ideally multiple independent trials each.
Record exact product/model versions, app visibility, system tool catalog,
skill version, model settings, latency, and token use. Do not substitute
manually imagined tool calls or the deterministic tests for model runs.

The neutral trace format accepts one JSON object per `case_id`, `client`,
`model`, and `trial`. **A client-side instrumented tool-call recorder** must
write `harness_recorded=true` with `tool_calls`; a model's own verbal
description of which tools it used is NOT evidence. A trace example (schematic,
not a measured result):

```json
{"case_id":"named-renewal","client":"client-label","model":"model-version","trial":"1","harness_recorded":true,"tool_calls":[{"name":"search_work","arguments":{"query":"Acme renewal"},"event_ids":["evt-renewal-001"],"response_chars":1350},{"name":"get_workflow_trace","arguments":{"since":"2026-10-08T00:00:00Z","until":"2026-10-09T00:00:00Z"},"event_ids":["evt-renewal-001"],"response_chars":3500}]}
```

An optional portable capture wrapper is included in
`evals/hosted_context/instrumentation.py`. After a real client invokes MCP,
record the returned tool payload (do not simulate the call) and append
a JSONL row:

```python
from pathlib import Path
from evals.hosted_context.instrumentation import MCPTraceRecorder

rec = MCPTraceRecorder(case_id="named-renewal", client="ChatGPT",
                       model="exact-observed-model-version", trial="1")
# actual_tool_result comes from a real client MCP invocation on test data.
rec.record(name="search_work", arguments={"query": "Acme renewal"},
           response=actual_tool_result)
rec.append_jsonl(Path("/tmp/owg-agent-eval-traces.jsonl"))
```

The recorder extracts event IDs only from **event-shaped rows**, strips the
search phrase, cursor, URLs and arbitrary tool arguments, and saves only tool
names, safe argument-presence flags, event IDs and response character counts.
Do not persist raw MCP responses or personal user data as evaluation artifacts.
The recorder cannot itself cause a model to call MCP; it observes a real call.

Use **synthetic events with those exact event IDs**, not any actual customer's
history, to construct each test tenant. Each `event_ids` list must be
extracted from the actual MCP response by the independent harness, not
self-reported by the AI. Clients should receive the SAME synthetic event
fixtures and allowed tool catalog, and run both OWG-enabled and OWG-absent
controls. A finished model run must cover all 22 cases. For event targets
older than the initial overview, only verified trace pagination counts;
`search_work` may find a keyword but does not replace raw fallback.

Run the scorer:

```bash
python -m evals.hosted_context.score --traces /path/to/client-traces.jsonl --out /path/to/report.json
python -m pytest -q tests/test_hosted_context_routing_score.py
```

**The scorer does not run an LLM or connect OAuth by itself**. It rejects
unmarked traces, invalid MCP tool names, duplicate case IDs and silent
omissions. It calculates:

- OWG invocation recall and precision; unnecessary invocation on generic
  prompts lowers precision, and no-call negative specificity is separate.
- First-tool accuracy and required follow-up tool recall.
- Raw fallback recall: `get_workflow_trace` **without a search query** for
  cases where inferred tasks or lexical indexing are unreliable.
- Expected synthetic event-ID coverage of *actual tool returns*.
- Tool count and response character budget; this is **not token usage**.
- Optional **independently adjudicated** supported/unsupported claims and
  unauthorized-action claims. Mere retrieval of an event ID does not prove
  a prose claim is entailed.

Provisional acceptance targets for **completed and externally verified**
model runs: ≥90% triggering recall; ≥90% invocation precision; 100% raw
fallback on deliberately false-negative inference cases; ≥95% human-reviewed
factual-claim support; zero inferred authorizations. Report CIs and
per-case failures across independent repetitions; if the sample is small,
avoid universal reliability claims. Compare models and clients by failure
family and end-task quality, not one aggregate percentage.

## Known boundaries

An MCP server does not automatically inject its evidence into every turn.
The model/client chooses when to invoke a tool. The local OWG recorder retains
richer history than the Gateway when the user has not shared it; the hosted
tool's "raw" chronology is still a **redacted hosted projection** of
authorized synchronized canonical *events*, never unrestricted agent prompt
text. Inferred tasks remain optional hints; no inferred match or zero lexical
matches never establish absence. New hosted output redaction is additive,
does not alter canonical local or Gateway records, and cannot guarantee
perfect detection of every possible personal identifier.

One private-plugin routing instruction still references release 0.122.0;
update its text and describe `detail=compact|rich` before relying on
"natural automatic tool invocation" in public evaluations. Pin the precise
installed plugin release and skill revision in every model result.
