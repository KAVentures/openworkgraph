# Offline agent-plan preflight (synthetic cases, no live actions)

OWG can generate a reviewed target process and a non-executable starter ZIP.
This adds an **opt-in local structural preflight** so an agent-built
*proposed plan* can be checked against explicit synthetic traces before a
business risks a live system. It does not run the agent or claim improvement
against APIs/logs alone.

## How to use

1. Export an agent starter from the human-reviewed AS-IS/TO-BE designer.
2. Have your implementation agent produce a **candidate plan** and separate
   **synthetic scenarios**. Keep all customer content and credentials out of
   this file. The preflight is not a reason to enable new source collection.
3. Compose one JSON request whose `workflow` is the ZIP's
   `workflow.spec.json`; `candidate` and `simulated_cases` are the
   new components documented below.
4. In **Evidence → Agent plan preflight**, select that JSON file and click
   **Evaluate synthetic plan**. Nothing is sent until that click; only the
   locally authenticated OWG process sees it, within a 64 KiB bound.
5. Inspect missing/duplicate steps, unapproved live writes, missing
   source-state checks, unverified success claims and simulated failures.

Minimal example (requires a full validated `workflow.spec.json` in
place of the abbreviated illustrative `workflow` below):

```json
{
  "format": "openworkgraph.agent-plan-preflight.v1",
  "workflow": {
    "format": "openworkgraph.agent-starter.v1",
    "review_status": "human_reviewed_design_not_authorized_execution",
    "security": {
      "live_actions_possible": false,
      "runtime_tool_permissions_granted": false,
      "source_records_included": false
    },
    "to_be": {
      "user_proposed_not_production_process": true,
      "automatic_decision_routing_enabled": false,
      "manual_review_conditions": [],
      "steps": [
        {
          "id": "step_001",
          "tool": null,
          "tool_binding_confirmed": false,
          "action_permission_granted": false
        }
      ]
    }
  },
  "candidate": {
    "format": "openworkgraph.agent-candidate-plan.v1",
    "intent": "shadow_only",
    "exception_handling": "human_escalation",
    "proposed_actions": [
      {
        "step_id": "step_001",
        "action_kind": "manual_review",
        "source_state_checked": false,
        "outcome_evidence_mode": "none"
      }
    ]
  },
  "simulated_cases": [
    {
      "scenario": "MissingEvidence",
      "mock_steps": [
        { "step_id": "step_001", "mock_result": "mock_unknown" }
      ],
      "claimed_outcome": "unknown"
    }
  ]
}
```

## What the validator actually checks

- The input starter is the **reviewed, non-executable** OWG format.
- The agent's candidate `intent` is `draft_only`, `shadow_only`
  or `live`; live intent is always flagged as a blocker.
- Each proposed step maps to an existing sequential `step_NNN` ID.
- `action_kind` is `read_only`, `draft_only`, `manual_review`, or
  `write`; writes are flagged as unauthorized and NEVER attempted.
- `outcome_evidence_mode` is `none`, `mock_only`, or
  `independent_source_readback` (these values describe the *candidate's
  proposal*, not verification by OWG).
- `exception_handling` is `not_configured`,
  `human_escalation`, or `mock_only`.
- Mock steps have `mock_success`, `mock_failure`, or `mock_unknown`;
  the claimed outcome is `unknown`, `claimed_success`, or
  `claimed_failure`. A success claim is always flagged as not
  independently verified, regardless of simulated evidence.
- 1–40 declared target steps; 1–24 synthetic scenarios; no external
  URLs, credentials or native customer/case IDs are required.

## Security and limitations

- Local dashboard-session authentication only; hosted AI/MCP bearer alone
  does not authorize this upload.
- Strict 64 KiB streamed input cap, no-store output, no persistence.
- No network/provider calls, model invocation, agent runtime, actions,
  employee recording, source-status checks, business outcome verification
  or live case joining occur.
- All results are sanitized structural diagnostics, not source labels or
  user-supplied goal content.
- `structural_review_passed` merely means that the submitted symbolic
  data did not hit this limited validator's checks. It is NEVER permission
  to deploy or evidence of a real agent passing a live case.
- Real A/B evaluation must separately compare a baseline against the agent
  using actual authorized tools, independently verified source receipts,
  matching case mixes, realistic exceptions, costs and failure recovery.

This is a foundation for an agent-testing harness, not the completed
end-to-end automation outcome gate.

The report can be downloaded as `openworkgraph-agent-preflight-report.json` and handed to an implementation agent for a revised candidate. The download carries only structural findings, not imported goal text or customer payloads, and is never sent automatically.

**Starter integration:** new agent-starter ZIP downloads include `preflight.request.template.json` with its own matching workflow specification, all actions set to `manual_review`, and deliberately `mock_unknown` synthetic observations. It is not a passing test. Have the implementation agent create realistic *synthetic* scenarios and fill in its candidate behavior, then upload the edited file to the local Evidence preflight.
