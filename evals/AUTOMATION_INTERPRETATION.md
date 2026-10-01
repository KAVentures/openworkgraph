# Automation interpretation evaluation

This eval checks a consuming AI for two opposite failures:

1. **Underestimation** — recommending old-style macros/templates, treating missing historical payload as a future blocker, or failing to see the next autonomy boundary around an existing agent.
2. **Overreach** — inventing automation where evidence is weak, deleting a step without checking downstream consumers, or inferring autonomous financial/clinical/high-impact authority from repetition.

The fixed cases live in `evals/automation_interpretation_cases.json`.

## Run protocol

Use the real OpenWorkGraph MCP entrypoint for the revision being evaluated, not a copied prompt string.

For each model/client under test:

1. Start a clean local OpenWorkGraph instance for the revision.
2. Connect the model through the real compact MCP server (`mcp_server.compact_stdio`).
3. Confirm initialization exposes the current server instructions, `openworkgraph://automation-capabilities`, and `find_automation_opportunities`.
4. For each fixed case, provide the case's `evidence_summary` as the scenario evidence and invoke the automation-analysis workflow. When a richer synthetic trace fixture exists for a case, prefer it over the prose summary.
5. Save the model's answer verbatim with the model/client/version and revision SHA.
6. Score every `must_cover` criterion and every `must_not` criterion on the shared 0/1/2 scale. For `must_not`, 2 means the failure was clearly avoided, 1 means ambiguous/partial, and 0 means the answer committed the failure.
7. Save the score sheet as JSON and run `python evals/score_automation_interpretation.py <scores.json>`.

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
