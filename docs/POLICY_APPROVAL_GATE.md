# Opt-in policy approval gate

OpenWorkGraph can now be placed deliberately in the execution path of an instrumented runtime for one narrow purpose: **pause a proposed structural action when explicit declared policy says a human-approval predecessor is missing**.

This is opt-in infrastructure. Existing OpenWorkGraph observers, browser capture, Claude Code hooks, Codex telemetry, OpenAI Agents tracing, MCP reads and custom agent-ingest clients do not start gating actions merely because this code exists.

## Why there is a separate credential

An agent that only needs a policy check should not receive the normal API/dashboard bearer, because that credential can read workflow history. It should also not reuse the agent-ingest token, because that token is intentionally write-only.

The approval gate therefore introduces a third local capability:

- `.agent_ingest_token` — structural telemetry **write only**;
- normal API/dashboard bearer — broad local **read/control** capability;
- `.policy_guard_token` — **read only for one structural policy advisory route**.

The dedicated route is:

`POST /policy-guard/v1/action-advisory`

It accepts only:

```json
{
  "family_key": "human:github.create_issue",
  "proposed_step": "tool:deployment:tool:aaaaaaaaaaaa",
  "completed_steps": ["approval_received:success"]
}
```

It cannot return observed work history, task context, prompts, tool arguments/results or model output. The response carries `capability_scope = "policy_action_advisory_only"` and preserves the advisory-only invariants from the normal `/v1/declared-policies/action-advisory` surface.

For explicit setup, the local token can be printed with:

```bash
python -m server.policy_guard_auth
```

The reusable client loads the installation-local token automatically when it is running locally. Remote use is disabled by default and requires both `OWG_ACTION_GUARD_ALLOW_REMOTE=1` and an explicit `OWG_POLICY_GUARD_TOKEN`.

## Reusable Python guard

`adapters.action_guard.OptInActionGuard` has two modes.

### `warn`

`warn` mode reads declared policy and returns/surfaces the structural warning, but a policy warning does not stop the wrapped action.

If the local policy-guard service is unavailable, warn mode also continues. Invalid integration input or a malformed response still raises rather than being mislabeled as service unavailability.

### `approval_gate`

`approval_gate` pauses only when the declared-policy advisory reports `approval_prerequisite_missing`.

When that happens:

1. the guard creates a structural `ApprovalRequest`;
2. it invokes the explicitly supplied `human_approval` callback;
3. only the literal Boolean `True` counts as approval;
4. after approval, it re-runs the policy advisory with structural approval steps added;
5. the wrapped action executes only if that second check no longer reports a missing approval prerequisite.

Strings such as `"yes"`, integers and other truthy values do not approve an action.

By default, policy-service unavailability in `approval_gate` mode pauses the action. An integrator may explicitly set `unavailable_behavior="continue"`, but that is an intentional fail-open choice rather than the default.

## Example

```python
from adapters.action_guard import OptInActionGuard


def ask_human(request):
    # Render whatever local UI your runtime uses. Sensitive local context can be
    # shown by that UI without being sent through OpenWorkGraph's policy request.
    return local_confirmation_dialog(request.as_dict())


guard = OptInActionGuard(
    mode="approval_gate",
    human_approval=ask_human,
)

result = guard.run(
    lambda: deploy_release(),
    family_key="human:github.create_issue",
    proposed_step="tool:deployment:tool:aaaaaaaaaaaa",
)

if not result.executed:
    handle_paused_action(result.decision.as_dict())
```

The action is a zero-argument closure on purpose. Its arguments, return value and exceptions are caller-local; the guard does not serialize them or send them to OpenWorkGraph.

## Optional structural approval evidence

A runtime may provide the existing `BufferedAgentEventSink` plus a structural `event_context`. When a real approval callback is invoked, the guard can emit:

- `human_approval_requested / running`;
- `human_approval_received / success` or `denied`.

The event context accepts only structural agent identifiers/metadata. Prompt content, tool arguments/results, reasoning and arbitrary nested runtime payloads are rejected by the existing agent-evidence contract.

These approval events are adapter assertions. OpenWorkGraph does not independently authenticate the identity of the person behind a custom runtime's callback.

Telemetry delivery remains best-effort and cannot decide whether the guarded action runs. The policy check and human decision are the gate; telemetry is observation.

## What is and is not enforced in this slice

This version deliberately enforces only a **missing approval predecessor** in `approval_gate` mode.

The following remain visible warnings rather than hard blocks:

- `forbidden_step`;
- non-approval `required_predecessor` failures;
- other declared-policy warnings.

That is intentional. A generic hard-deny engine changes the risk profile substantially and should be introduced separately with explicit administrator opt-in, recovery semantics, policy-version pinning and more adversarial testing.

Observed workflow repetition is never an allow-list or permission source. Approval-frequency observations are not converted into policy. Context/outcome associations are not used as authorization evidence.

## Failure semantics

| Situation | `warn` | `approval_gate` default |
| --- | --- | --- |
| No matching declared constraint | execute | execute |
| Declared warning, no missing approval | execute with warning | execute with warning |
| Missing declared approval predecessor | execute with warning | request human approval |
| Human denies | n/a | do not execute |
| Approval callback errors | n/a | do not execute |
| Approval callback returns non-Boolean | n/a | do not execute |
| Approval succeeds but recheck still requires approval | n/a | do not execute |
| Policy guard unavailable | execute, marked unavailable | do not execute |

## Compatibility boundary

This feature does not:

- intercept arbitrary tool calls globally;
- alter Claude/Codex/OpenAI adapters by default;
- modify the desktop collector or browser observer;
- change the database schema;
- change Gateway behavior;
- grant the write-only agent token any read permission;
- grant the policy-guard token access to workflow history;
- turn observed behavior into policy;
- implement hard deny for forbidden actions.

A runtime must instantiate and call `OptInActionGuard` around the action it wants guarded. Without that explicit integration, execution behavior is unchanged.
