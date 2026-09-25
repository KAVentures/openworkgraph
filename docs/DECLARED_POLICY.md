# Declared policy / SOP plane

OpenWorkGraph separates two fundamentally different things:

1. **Observed procedural memory** — what humans and agents have actually been seen doing.
2. **Declared policy** — what an organization explicitly says should happen.

Observed repetition never becomes policy automatically. The declared-policy plane exists so an agent can receive both sources while preserving their different authority.

## Trust boundary

Declared policy is loaded from a local JSON manifest. OpenWorkGraph does **not** provide a policy-write REST or MCP API in this version.

Default path:

```text
data/declared_policies.json
```

Override it with:

```text
WORKFLOW_OBSERVER_POLICY_FILE=/path/to/declared_policies.json
```

The file must already exist if policy is to be used. An absent file means `manifest_present=false`; existing observer functionality continues normally.

Because agents cannot modify this file through OpenWorkGraph, agent telemetry cannot rewrite the policy against which it is compared.

## Machine-readable structural policy

The manifest is intentionally not free-form prose and is not interpreted by an LLM.

Supported rule types:

### `required_step`

A structural step is declared required.

```json
{
  "rule_id": "approval-required",
  "type": "required_step",
  "step": "approval_request"
}
```

### `forbidden_step`

Observation of the structural step is treated as a potential divergence.

```json
{
  "rule_id": "no-cancel",
  "type": "forbidden_step",
  "step": "error:cancelled"
}
```

### `required_predecessor`

If the trigger step is observed, the required step must have appeared earlier.

```json
{
  "rule_id": "approval-before-submit",
  "type": "required_predecessor",
  "required_before": "approval_received:success",
  "trigger_step": "action:submit"
}
```

Only structural step tokens OpenWorkGraph itself can generate are accepted. Arbitrary prose, prompt-like identifiers and instruction-like strings are rejected.

## Manifest example

See [`examples/declared_policies.example.json`](../examples/declared_policies.example.json).

Each policy contains:

- `policy_id` — stable machine identifier;
- `version` — explicit version identifier;
- `status` — `active`, `draft`, or `retired`;
- `family_key` — the procedural family the policy applies to;
- `source_type` — `manual_sop`, `repository_policy`, or `external_reference`;
- `source_ref` — external provenance reference;
- `rules` — structural rules.

Only one `active` policy is allowed for a given `family_key`.

The raw `source_ref` is never returned through the API. OpenWorkGraph exposes a stable hash instead, together with the manifest SHA-256, so consumers can identify provenance/version without receiving the potentially sensitive reference string.

## Comparison semantics

A policy comparison can return:

- `compliant`
- `potential_divergence`
- `insufficient_observation`
- `not_applicable`

The word **potential** is important. OpenWorkGraph does not claim that a divergence proves misconduct, causality, or even complete visibility.

### Missing evidence is treated conservatively

For rules where absence matters, OpenWorkGraph only uses missing evidence as a potential divergence when the execution has strong enough observation coverage:

- `native_trace`
- `instrumented_tools`

For weaker coverage such as `os_observed`, a missing required step becomes `insufficient_observation` rather than a policy violation.

Positive evidence is different. If a forbidden step is actually observed, it can be reported as a potential divergence even when the overall observation depth is weaker.

## REST

All policy reads require the ordinary local API read bearer. The write-only agent-ingestion credential cannot use them.

```text
GET /v1/declared-policies
GET /v1/declared-policies/compare?family_key=...
GET /v1/procedural-memory/governed-context-pack?family_key=...
```

`/v1/declared-policies` lists the validated, privacy-minimized manifest.

`/v1/declared-policies/compare` compares the active declared policy for one family against canonical observed executions.

`/v1/procedural-memory/governed-context-pack` returns the bounded observational context pack from the procedural-memory layer plus the separate declared policy and policy/observation comparison.

## Authority separation

The governed context pack explicitly states:

```json
{
  "declared_policy_is_normative_input": true,
  "observed_behavior_is_policy": false,
  "policy_inferred_from_behavior": false,
  "automatic_enforcement": false
}
```

A repeated workflow is therefore never promoted into an SOP merely because it is common.

Similarly, a declared policy does not erase contradictory observations. Both are returned separately, enabling a consumer to see cases such as:

```text
Declared policy: approval_request is required.
Observed evidence: 18 comparable executions included approval; 6 strongly observed executions did not.
Interpretation: potential policy/observation divergence requiring human review.
```

## Failure behavior

- Missing manifest: policy layer returns no declared policy; existing OpenWorkGraph operation continues.
- Invalid manifest: policy endpoints fail closed with HTTP 422.
- The malformed policy file does not prevent the observer server or unrelated endpoints from running.
- No database migration is involved.
- No background policy learner exists.
- No automatic enforcement exists.
- No agent can mutate policy through this API.

## Why policy is separate from procedural memory

Procedural memory answers:

> What has happened before?

Declared policy answers:

> What has explicitly been declared to be the rule?

The governed context pack can carry both without pretending they are the same source of truth.
