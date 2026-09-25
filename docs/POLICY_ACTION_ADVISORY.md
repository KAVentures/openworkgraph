# Declared-policy action advisory

OpenWorkGraph can evaluate explicit declared structural policy immediately before an agent or integration proposes a structural workflow step.

This is an **advisory read**, not an enforcement engine.

## Surfaces

Authenticated local REST:

`POST /v1/declared-policies/action-advisory`

The POST body is only a bounded structured query:

```json
{
  "family_key": "human:github.create_issue",
  "proposed_step": "tool:deployment:tool:aaaaaaaaaaaa",
  "completed_steps": ["approval_received:success"]
}
```

The endpoint performs no writes and does not execute the proposed action.

Secure MCP:

`get_action_policy_advisory`

MCP access still requires OpenWorkGraph AI access to be enabled for the current run. The tool is audited like the other secure MCP reads.

## What is evaluated

The advisory reuses the existing declared-policy structural rule language.

### `forbidden_step`

If the proposed step exactly matches a declared forbidden step, the response contains a `declared_forbidden_step` warning.

This still does not block the caller.

### `required_predecessor`

If the proposed step is the rule's `trigger_step`, OpenWorkGraph checks whether `required_before` is present in the supplied completed structural steps.

The result is either:

- `prerequisite_satisfied`;
- `prerequisite_missing`; or
- `approval_prerequisite_missing` when the required structural predecessor is an approval request/receipt step.

This is the rule type that expresses ordering. OpenWorkGraph does not invent ordering from other rule types.

### `required_step`

A generic required step means only that the declared policy requires that step somewhere in the governed workflow. It has no implicit deadline or ordering semantics.

Therefore it is relevant to action advisory only when the proposed action is that required step itself. It is **not** converted into a prerequisite for unrelated actions.

## Advisory states

`declared_policy_warning`
: one or more explicit declared constraints are currently unsatisfied or the proposed step is explicitly forbidden.

`matched_constraints_satisfied`
: the proposed step matches declared constraints and the structural prerequisites supplied by the caller satisfy them.

`no_matching_declared_constraint`
: an active policy exists, but none of its structural rules constrains this proposed step.

`no_active_declared_policy`
: no active declared policy exists for the supplied family.

Neither of the last two states means that the action is authorized.

## No authorization semantics

Every response deliberately states:

- `authorization_decision = "not_made"`;
- `action_allowed = null`;
- `blocking = false`;
- `automatic_enforcement = false`;
- `execution_performed = false`.

A missing matching rule must never be interpreted as an allow rule.

## Authority separation

The advisory uses only explicit declared policy as normative input.

It does **not** use:

- repeated observed behavior as permission;
- approval-frequency observations as a policy requirement;
- prior successful runs as an allow-list;
- context/outcome associations as policy.

The response states `observed_work_used_as_permission = false` and `observed_behavior_considered = false`.

This keeps the architecture established by the procedural-memory and declared-policy layers:

- declared policy says what the organization explicitly declares;
- observed workflow says what has been observed;
- neither silently changes the authority of the other.

## Security and privacy

Only OpenWorkGraph-generated structural step tokens are accepted. Arbitrary natural-language instructions are rejected.

The normal API read credential can use the endpoint. The write-only agent-ingest credential cannot.

Policy source references are not exposed raw; the normalized declared-policy metadata uses the existing source-reference hash.

## What this PR intentionally does not do

This layer does not:

- intercept tool calls;
- pause an agent;
- request an approval itself;
- approve an action;
- deny an action;
- modify a policy;
- create a policy from observed behavior;
- execute the proposed action.

A later guardrail layer can build explicit warn / approval-gate / deny semantics on top of this advisory contract, but those modes require separate opt-in execution-path integration and a higher correctness bar.
