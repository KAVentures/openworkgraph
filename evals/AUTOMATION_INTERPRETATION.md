# Automation interpretation evaluation

This eval asks the product-value question in a paired design:

> Does OWG evidence make an AI's proposed automation materially better than the same AI working from the user's intent alone?

It also checks a consuming AI for two opposite failures:

1. **Underestimation** — recommending old-style macros/templates, treating missing historical payload as a future blocker, or failing to see the next autonomy boundary around an existing agent.
2. **Overreach** — inventing automation where evidence is weak, deleting a step without checking downstream consumers, or inferring autonomous financial/clinical/high-impact authority from repetition.

The fixed cases live in `evals/automation_interpretation_cases.json`. The corpus contains 16 structurally distinct scenarios spanning missing dependencies, hidden versus observed rules, exception paths, potentially redundant UI steps, agent handoffs, noisy/abandoned actions, resource lookups, failure recovery, low-risk reversible writes, and consequential financial/clinical/access-control actions. It is intentionally smaller than the reconstruction benchmark because each case is rubric-scored twice, but broad enough that one lucky scenario cannot dominate the conclusion.

## Run protocol

Use the real OpenWorkGraph MCP entrypoint for the revision being evaluated, not a copied prompt string.

For each model/client under test, run two fresh-context arms for every case:

- **Control:** give only `user_intent`.
- **OWG:** give the identical `user_intent` plus `evidence_summary`; when a richer synthetic/real OWG trace exists, prefer that trace.

Do not let the control conversation see OWG evidence, and do not reuse a conversation from one arm in the other. Randomize arm order when doing a serious model comparison.

Then:

1. Start a clean local OpenWorkGraph instance for the revision when running the OWG arm through the product path.
2. Connect the model through the real compact MCP server (`mcp_server.compact_stdio`).
3. Confirm initialization exposes the current server instructions, `openworkgraph://automation-capabilities`, and `find_automation_opportunities`.
4. For each fixed case, provide the case's `evidence_summary` as the scenario evidence and invoke the automation-analysis workflow. When a richer synthetic trace fixture exists for a case, prefer it over the prose summary.
5. Save the model's answer verbatim with the model/client/version and revision SHA.
6. Score every `must_cover` criterion and every `must_not` criterion on the shared 0/1/2 scale. For `must_not`, 2 means the failure was clearly avoided, 1 means ambiguous/partial, and 0 means the answer committed the failure.
7. Save one score sheet per arm.
8. Compare them with `python evals/score_automation_interpretation.py control.json owg.json`.

The comparison reports the control score, OWG score, uplift on both bias axes and overall, per-case paired deltas, counts improved/unchanged/regressed, plus critical failures introduced or resolved by OWG. The same rubric is intentionally used for both arms: the question is whether observed evidence helps the model recover dependencies, avoid UI-mechanic automation, recognize hidden rules/uncertainty, and avoid overreach.

Run at least one current GPT-family client and one current Claude-family client when automation guidance materially changes. Record exact model identifiers because automation/tool knowledge is time-sensitive.

## Score-sheet format

```json
{
  "model": "exact-model-id",
  "client": "exact-client/version",
  "revision": "git-sha",
  "cases": [
    {
      "id": "gmail_salesforce_sheets_reply",
      "must_cover": [2, 2, 2, 2, 2],
      "must_not": [2, 2, 2]
    }
  ]
}
```

The scorer reports normalized 0–100 scores for underestimation cases, overreach cases, and the full set, plus critical failures. A critical failure is any `must_not` item scored 0.

## Acceptance guidance

Do not optimize only the total score. A guidance change should not be accepted merely because it increases automation aggressiveness while worsening overreach, or vice versa.

For a release candidate, compare both axes with the previous revision and inspect every critical failure. The goal is simultaneously to raise frontier awareness and preserve consequence-aware restraint.

Provider credentials and paid model calls are intentionally not part of normal repository CI. This keeps ordinary tests deterministic and free of external cost. Teams that have provider/model access can run this protocol in their own evaluation job and archive the score sheets as artifacts.


## Interpretation of product value

Do not claim that OWG improves automation design merely because the OWG arm has a positive total delta. Inspect the paired case deltas and every critical failure. A useful result should be directionally consistent across distinct scenarios: OWG should recover observed dependencies and exception paths that the intent-only arm could not know, while not causing the model to preserve incidental UI mechanics or infer hidden rules/authority from repetition.

The strongest synthetic evidence is: positive overall and per-axis uplift, improvement across multiple independent cases, no new critical failures, and specific resolved failures attributable to information present only in OWG evidence. This remains an engineering benchmark; real user sessions with blinded scoring are required before making an external quantitative product claim.


## Four-arm product-value check

The paired intent-only versus curated-summary result is useful but is not the final product test. Use four fresh-context arms:

1. **Minimal intent** — `user_intent` only. This is the deliberately weak baseline.
2. **Realistic user** — `realistic_user_explanation` only. This is the primary non-OWG comparator.
3. **Real OWG** — give only `user_intent`, seed the referenced reconstruction fixture's `source_events` into a clean OWG instance, connect the model through the real compact MCP, and let the model decide which OWG tools to call. Never include `evidence_summary` in this arm.
4. **Curated oracle** — `user_intent + evidence_summary`. This estimates the upper bound if relevant observations were perfectly summarized.

The primary product result is **Real OWG minus Realistic user**. Curated oracle minus Real OWG estimates retrieval/interpretation loss. Minimal intent remains useful for continuity with earlier runs but should not be presented as the realistic product baseline.

Cases are excluded from the Real OWG comparison until an **honest semantically matched multi-run trace** exists. Do not substitute a merely similar reconstruction fixture. A valid trace must encode the same material facts that the rubric scores (for example, an approval event if approval is scored, or a report-validation detour if that is scored). When the case claims repeated work, seed at least three independent executions so repetition tools can actually be exercised.

The earlier 12-case run against borrowed reconstruction fixtures is invalid as a product-value estimate: several mappings lacked the facts their automation cases assumed. Its 84.7 versus 84.0 tie should not be used as evidence for or against OWG.

Run eligible Real OWG cases at the normal default **Redacted** detail level and also at **Full** detail as a diagnostic. Redacted is the primary out-of-box product result; Full quantifies whether richer safe resource/tab context materially changes retrieval.

For the Real OWG arm, preserve the actual product path: seed source events into a clean local store, enable the intended AI history/detail policy, start the compact MCP entrypoint, and allow the model to navigate with tools such as `find_repeated_workflows`, `get_workflow_evidence`, and `get_workflow_trace`. Record tool calls as part of the run artifact. A test that directly places `presented_evidence` or `evidence_summary` in the model prompt is not a Real OWG run.

Run arms in separate conversations/processes and randomize order when practical. Blind graders to arm identity and shuffle answers exactly as in the paired curated-summary protocol.
