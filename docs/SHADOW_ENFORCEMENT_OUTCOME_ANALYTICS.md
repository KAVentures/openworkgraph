# Shadow-enforcement outcome analytics

OpenWorkGraph can record a privacy-minimized assertion that an instrumented agent evaluated the #67 shadow-enforcement preview before a tool call that actually proceeded, then derive aggregate associations between those preview dispositions and the structural terminal outcome of the containing run.

This is **not enforcement** and it is **not an enforcement-effect study**.

## What is recorded

An integration may attach a `shadow_enforcement` object to an agent `tool_call` event. The canonical evidence contract accepts only:

- the fixed preview profile ID `declared-policy-shadow-v1`;
- whether the policy advisory was available;
- the candidate disposition;
- the structural workflow family key;
- an optional SHA-256 fingerprint of the declared-policy manifest;
- explicit flags stating that the preview was simulation-only and did not enforce or block anything.

The event cannot include policy text, rule text, human identity, action arguments, action results, prompts, model responses, or reasoning in this structure. The existing recursive agent-evidence privacy checks remain in force.

Shadow telemetry is accepted only on `tool_call` events because this slice describes a preview associated with an action that actually proceeded in shadow mode.

The assertion is marked as adapter-reported. OpenWorkGraph does not claim that the server independently attested that the model or runtime consulted the preview.

## Attaching a #67 preview

```python
from adapters.shadow_link import attach_shadow_preview_to_tool_call

agent_event = attach_shadow_preview_to_tool_call(agent_event, preview)
```

This returns a copy of the structural tool-call event with the privacy-safe shadow block attached. Nothing is emitted automatically by the existing Claude Code, Codex, OpenAI Agents SDK or other adapters.

## Read-only aggregate endpoint

`GET /v1/shadow-enforcement/outcome-associations`

The endpoint uses the ordinary authenticated API/dashboard read credential. The write-only agent-ingest credential cannot read it.

Optional query parameters:

- `family_key` — restrict to a structural workflow family;
- `since` — restrict canonical evidence by time;
- `evidence_limit` — bounded evidence read;
- `min_stratum_support` — minimum unique runs before a family/observation-level/disposition stratum is returned;
- `max_strata` — output bound.

The endpoint performs no writes.

## Event counts versus run counts

A run can contain several shadow previews. For example, a run may attempt the same tool twice and receive `candidate_deny` both times.

The analytics therefore distinguish:

- **preview event count** — how many shadow previews were recorded;
- **run count** — how many distinct executions contained that disposition.

Repeated same-disposition previews in one run count multiple times as preview events but once in that disposition's run-outcome summary.

A run that contained different dispositions can appear in more than one disposition summary. The combined `candidate_interrupt_run_outcomes` summary deduplicates the run across the interrupt-like dispositions.

## Candidate interrupt dispositions

The fixed shadow profile treats these as dispositions that a future strict profile would interrupt:

- `candidate_deny`;
- `candidate_pause_for_human_approval`;
- `candidate_pause_for_prerequisite`.

That label remains hypothetical. `actual_enforcement_enabled` and `actual_blocking_observed` remain false in this slice.

## Outcomes

The analytics relate a preview to the **terminal outcome of the containing agent run**, not to an action-level outcome.

Outcomes remain structural:

- explicit `success`;
- explicit failure (`error`, `denied`, or `cancelled`);
- `unknown` when no explicit terminal outcome was observed.

Unknown is never converted to failure or success.

Approval-request and approval-received counts are also structural observations on the containing run.

## No false-positive or causal claim

A run succeeding after a `candidate_deny` preview does **not** prove the candidate deny was a false positive. The action may have been harmless, later remediation may have occurred, the run-level success may not represent the business outcome, or the declared policy may intentionally prohibit successful actions.

Likewise, a later failure does not prove that blocking the previewed action would have prevented that failure.

Responses therefore explicitly set:

- `causal_interpretation = false`;
- `effect_estimate = false`;
- `false_positive_rate_estimate = false`;
- `authoritative = false`.

OpenWorkGraph does not calculate enforcement efficacy, safety benefit, false-positive rate, or false-negative rate from these observational data.

## Stratification

Family/observation-level/disposition strata are returned only after the configured minimum number of unique runs. Preview-family mismatches and executions without an observed family are counted descriptively but excluded from these same-family strata.

This helps expose workflow mix without pretending that pooled differences are enforcement effects.

## Privacy

The aggregate response does not return:

- native run/session/trace/span IDs;
- per-run identifiers;
- tool names or tool arguments/results;
- context fingerprints;
- policy-manifest fingerprint values;
- prompts, model responses, or reasoning.

It may report how many preview events carried a policy-manifest fingerprint, but not the fingerprint itself.

## Scope of this slice

This feature does not:

- enable hard deny;
- pause an action;
- modify #65 approval gating;
- modify #66 policy-bound approval receipts;
- automatically instrument any native adapter;
- add a database table or schema migration;
- add dashboard or MCP behavior;
- change the desktop/browser observer, Gateway, launchers, or existing human-workflow functionality.
