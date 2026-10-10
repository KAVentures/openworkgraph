# Observed real-client evaluation — 2026-10-10

**Verdict: inconclusive. Automatic invocation and material work-continuity improvement were not demonstrated or disproved. Neither provider accepted a model inference request.** This report does not claim verified ChatGPT-web or Claude-web behavior.

## What actually executed

Repository source: `ef3ad4004155a92a3d54ce05dca0e5c8c86746cd` (OpenWorkGraph 0.123.0). Original cases and fixtures were preserved for the primary experiment. Their hashes are in the manifest. Codex CLI **0.159.2** and Claude Code **2.1.296** were executed, not simulated. Claude Code was installed into a disposable prefix because it was initially absent.

All **22 original scenarios**, including 16 positives and 6 negative controls, were launched with both clients in both conditions: **88 fresh independent CLI sessions** in the final matrix. Enabled and disabled sessions used identical original prompts, requested models and medium effort. Original onboarding prompts already name OWG or ChatGPT; the remaining prompts were not supplemented with routing instructions. Native client system prompts differ across clients.

| Client | Requested model identifier | Enabled attempts | Disabled attempts | Completed inference sessions | Authentication finding |
| --- | --- | ---: | ---: | ---: | --- |
| Codex | `gpt-5.4` | 22 | 22 | 0 | HTTP 401; access token could not be refreshed |
| Claude Code | `claude-sonnet-4-6` | 22 | 22 | 0 | Not logged in; authentication_failed |

Requested identifiers are **not verified immutable model versions**. Claude initialization echoed `claude-sonnet-4-6`, but no inference occurred; Codex did not report an accepted backend model. Exact evaluated model snapshots are therefore unavailable. A Codex login-status check reported logged in, but actual provider requests rejected it. The managed environment reported no configured provider secrets. No credentials were requested in chat, logged, or committed.

Every enabled final attempt initialized the seven-tool hosted MCP catalog successfully (**44/44**). Claude's structured initialization listed only those seven tools and reported the server connected. The independent proxy recorded **zero model-origin MCP calls**. Catalog initialization is not invocation recall. Every negative control was also blocked before inference; none counts as successful abstention.

A preliminary 88-attempt matrix also stopped on authentication, while a harness JWKS startup defect prevented catalog initialization. It is retained as an explicitly excluded attempt ledger. The final matrix ran after fixing that defect and the proxy's MCP v2 result-field handling. Total submitted CLI attempts across preliminary and final stages: **176**, with **zero completed model turns**. No failed session has `harness_recorded=true`; final `traces.jsonl` is intentionally empty and `routing.json` reports no observed model results.

## Metrics

| Requested measurement | Observed estimate | Reason |
| --- | --- | --- |
| Automatic invocation recall | unmeasured | No completed positive model turns |
| Invocation precision | unmeasured | No model-origin calls |
| Negative-control specificity | unmeasured | No completed negative model turns |
| First-tool accuracy | unmeasured | No model-origin calls |
| Required follow-up tool recall | unmeasured | No completed positive model turns |
| Query-free chronological fallback recall | unmeasured | No completed fallback turns |
| Correct-event retrieval by model | unmeasured | No model-origin retrieval |
| Unsupported claims / inferred authorization | unmeasured | No substantive model answers |
| Paired downstream task-completion improvement | unmeasured | Neither condition produced evaluable answers |
| Confidence intervals | unavailable | Zero evaluable model trials |

The unmeasured values must not be replaced with zero or 100%. Routing correctness and downstream usefulness require separate grading. The README defines an independent answer-review rubric and notes the existing scorer's vacuous required-tool successes and combined positive/negative first-tool metric. The corpus has no live source-system connectors, so evidence-based answers can be assessed after authentication; completion of real business actions cannot.

## Each scenario's outcome

Columns indicate enabled/disabled for each client. Every row is an authentication limitation, not a behavioral failure.

| Scenario | Kind | Codex enabled | Codex disabled | Claude enabled | Claude disabled |
| --- | --- | --- | --- | --- | --- |
| recent-continuity | positive | auth blocked | auth blocked | auth blocked | auth blocked |
| ambiguous-resume | positive | auth blocked | auth blocked | auth blocked | auth blocked |
| named-renewal | positive | auth blocked | auth blocked | auth blocked | auth blocked |
| raw-without-task | positive | auth blocked | auth blocked | auth blocked | auth blocked |
| no-lexical-match | positive | auth blocked | auth blocked | auth blocked | auth blocked |
| misleading-adjacency | positive | auth blocked | auth blocked | auth blocked | auth blocked |
| older-history | positive | auth blocked | auth blocked | auth blocked | auth blocked |
| many-events | positive | auth blocked | auth blocked | auth blocked | auth blocked |
| workflow-automation | positive | auth blocked | auth blocked | auth blocked | auth blocked |
| workflow-repeated-empty | positive | auth blocked | auth blocked | auth blocked | auth blocked |
| agent-retry | positive | auth blocked | auth blocked | auth blocked | auth blocked |
| handoff | positive | auth blocked | auth blocked | auth blocked | auth blocked |
| resource-pointer-only | positive | auth blocked | auth blocked | auth blocked | auth blocked |
| unavailable-current-state | positive | auth blocked | auth blocked | auth blocked | auth blocked |
| empty-gateway | positive | auth blocked | auth blocked | auth blocked | auth blocked |
| local-only | positive | auth blocked | auth blocked | auth blocked | auth blocked |
| generic-arithmetic | negative | auth blocked | auth blocked | auth blocked | auth blocked |
| generic-literature | negative | auth blocked | auth blocked | auth blocked | auth blocked |
| prospective-planning | negative | auth blocked | auth blocked | auth blocked | auth blocked |
| general-coding | negative | auth blocked | auth blocked | auth blocked | auth blocked |
| no-work-request | negative | auth blocked | auth blocked | auth blocked | auth blocked |
| medical-factual | negative | auth blocked | auth blocked | auth blocked | auth blocked |

## Isolation and independent observation

The controller ran outside each evaluated session. Fresh empty working directories contained no repo instructions, expected IDs, fixture files, controller prompts, or previous conversations. Built-in shell/data-access tools, external connectors and subagents were disabled; no session was resumed. The enabled server's production tool descriptions, schemas and initialization instructions were forwarded unchanged. This is the **hosted-tool-only** installation condition, without local-desktop skills. It is not a universal test of all client installations.

A new SQLite Gateway bound only to loopback ingested **246 synthetic events** using single-use enrollment grants, enrolled device tokens and the normal batch API. Structural agent sharing was explicitly opted in only for the test identity and disabled after ingestion. Two other accounts contained no synced evidence. Genuine RS256 OAuth tokens passed the production verifier and actor scoping. No production Gateway was contacted or seeded, no desktop observer was installed, and no private captured activity was accessed. Test-only loopback HTTP and a synthetic HTTPS resource identifier do not verify public HTTPS/OAuth deployment behavior.

The transparent stdio proxy observes actual transport requests and returned event-shaped IDs, not the model's claims. Temporary credentials, Gateway databases and raw streams are removed after collection. Committed files contain sanitized structure, original synthetic prompts, catalog descriptions and versions, no OAuth/API secrets and no raw MCP evidence payloads.

## Infrastructure evidence, separate from model evidence

**17 regression tests passed** (`tests/test_real_hosted_context_clients.py` and `tests/test_hosted_context_routing_score.py`). The real authenticated proxy forwarded the unchanged catalog, traversed 246 events over ten pages, retrieved every golden target ID, returned empty onboarding evidence for both empty accounts, and rejected missing bearer authentication with HTTP 401. These findings demonstrate the evaluation transport works; they cannot demonstrate that an AI chooses it automatically.

The controller's separate fixture audit found validity defects in the original benchmark:

- `no-lexical-match`: searching `special request` actually returns `evt-unmatched-003`; it does not establish the advertised lexical miss.
- `many-events`: October 6 contains only events 001 and 044, so a date-bounded answer sees two events rather than a pagination stress test.
- `resource-pointer-only`: the pricing row says only “Spreadsheet pricing sheet”; `Acme pricing` returns no match and the fixture does not establish that association.
- `agent-retry`: the single structural event has `action=edit`, but run discovery requires `metadata.operation`; `get_agent_runs` returns zero runs.
- `ambiguous-resume`: the newest 15 events are filler switches, while the golden target is an earlier editor event; latest-work target correctness needs a stronger oracle.
- `workflow-automation`: the audit returns zero candidates; two quotation title strings are not a demonstrated multi-step quotation workflow.

These are fixture/oracle limitations, not demonstrated model routing failures. Narrow synthetic-fixture corrections and regression checks belong in a separate PR. The original-corpus result is preserved. No change to product logic, tool trigger descriptions or privacy policy is warranted from these blocked runs.

## Costs, artifacts and remaining work

Claude reported **$0** and zero input/output tokens for all 44 final attempts. Codex supplied no completed-turn token/cost accounting: its cost is **unknown**, not asserted to be $0. No provider invoice reconciliation was performed. The initial standalone Claude authentication probe also reported $0. Installation and infrastructure compute costs are not exposed by these clients and are not estimated. Per-attempt latency and reported usage are saved in `attempts.jsonl`.

The final directory contains `manifest.json`, `attempts.jsonl`, `catalogs.json`, `seed.json`, empty `traces.jsonl`, and `routing.json`. The neighboring `fixture-audit.json`, `validation.json`, dependency lock and README make the checks repeatable. Restoring provider authentication is necessary to measure AI behavior. After that, use independent repeated trials, review answers separately from routing traces, and report paired enabled/disabled task outcomes with uncertainty. The existing synthetic corpus and small negative-control set cannot establish universal reliability.

**Demonstrated:** isolated authenticated MCP transport, catalog availability in both CLIs, deterministic fixture retrieval, and provider authentication blockers. **Untested:** autonomous model recognition, automatic invocation accuracy, factual answer grounding, and material improvement in work continuity. OpenWorkGraph has not earned a positive continuity verdict from this experiment; the absence of model inference also does not support a negative verdict.
