# Shadow enforcement preview

OpenWorkGraph can now simulate how a future declared-policy enforcement profile would classify a proposed structural action **without enforcing anything**.

This feature is intentionally opt-in and adapter-local. Nothing imports or activates it automatically.

## Purpose

The preview is the safety step between advisory/approval gating and any future hard-deny mode. It lets an integration observe what a strict declared-policy profile would have done while the real action still follows the caller's existing behavior.

The fixed preview profile is identified as:

`declared-policy-shadow-v1`

Its mapping is deliberately small:

- matching declared `forbidden_step` -> `candidate_deny`;
- missing human-approval predecessor -> `candidate_pause_for_human_approval`;
- missing non-approval predecessor -> `candidate_pause_for_prerequisite`;
- matched/satisfied declared constraints -> `no_blocking_condition_observed`;
- no matching constraint or no active policy -> `no_declared_enforcement_decision`;
- policy advisory unavailable -> `indeterminate_policy_unavailable`.

These are **simulation labels**, not authorization decisions.

## No allow decision

The preview never returns "allowed". In particular:

- no policy does not mean allowed;
- no matching rule does not mean allowed;
- a satisfied prerequisite does not mean the organization authorized the action;
- observed workflow behavior is not used as permission.

Every preview keeps:

- `authorization_decision = not_made`;
- `action_allowed = null`;
- `actual_enforcement_enabled = false`;
- `actual_blocking = false`;
- `simulated_only = true`.

## Shadow execution

`ShadowEnforcementSimulator.run(...)` evaluates the preview and then invokes the caller's zero-argument action regardless of the candidate disposition.

That means even a `candidate_deny` result does **not** block execution in this slice.

Policy-service unavailability is represented as indeterminate and also does not block shadow execution.

Invalid structural caller input or a malformed advisory response still raises as an integration error before the action runs; those cases are not policy decisions and are not silently converted into permission.

## Example

```python
from adapters.enforcement_preview import ShadowEnforcementSimulator

simulator = ShadowEnforcementSimulator()

result = simulator.run(
    deploy,
    family_key="agent:workflow:0123456789abcdef",
    proposed_step="tool:deployment:tool:0123456789abcdef",
)

print(result.preview.candidate_disposition)
# e.g. "candidate_pause_for_human_approval"

assert result.executed is True
```

## Reporting callback

An integration may provide `on_preview=...` to display or collect the preview. Callback failures are swallowed so reporting cannot accidentally become an enforcement source.

This PR does not persist preview results to the OpenWorkGraph database and adds no new endpoint, credential, telemetry type, dashboard behavior, or MCP tool.

## Relationship to existing guards

This feature does not replace or modify:

- `OptInActionGuard` from the approval-gate layer;
- `PolicyBoundApprovalGuard` and policy-bound approval receipts;
- the declared-policy advisory endpoint;
- existing human or agent observation.

A future true enforcement layer should be a separate explicit opt-in step after organizations have reviewed shadow results and the semantics are stable.
