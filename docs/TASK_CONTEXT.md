# Unified task context

OpenWorkGraph exposes one read-only context surface for agents that need to understand how a task is governed and how similar work has actually been performed.

REST:

```text
GET /v1/task-context
```

Secure local MCP:

```text
get_task_context
```

The surface composes existing declared-policy and procedural-memory primitives. It does not create another learned-memory store and does not change capture, evidence, policy authoring, or policy distribution.

## The authority boundary

Task context deliberately keeps four different kinds of information separate:

1. **Declared policy** — normative input when an active policy exists for the procedural family.
2. **Observed procedure** — derived, non-authoritative evidence about repeated human or agent executions.
3. **Policy/observation comparison** — a derived structural assessment. A potential divergence is not proof of misconduct, intent, causality, or complete observation.
4. **Family resolution** — routing metadata used only to select which family to retrieve.

Repeated behavior never becomes policy merely because it is common. Declared policy is never rewritten from observed behavior. Retrieval does not execute a workflow and does not enforce policy.

The task-context reader also does **not** claim that a declared policy was cryptographically verified. Enterprise signed-policy verification belongs to the managed endpoint layer described in [Enterprise policy signing and distribution](ENTERPRISE_POLICY_SIGNING.md). Task context reads the active declared-policy manifest after that separate trust decision.

## Conservative family resolution

The first version intentionally does not fuzzy-match arbitrary natural-language task descriptions.

Resolution order:

- `family_key`: an explicit OpenWorkGraph procedural family key. This is the strongest selector and may be used before any prior run exists.
- `task_family`: a canonical human task family such as `email.reply` or `github.review`; it maps deterministically to `human:<task_family>`.
- `current_steps`: OpenWorkGraph-generated structural step tokens. Automatic resolution requires at least two steps and succeeds only if those steps uniquely prefix one observed family's dominant sequence.

If structural evidence matches more than one family, the response is `ambiguous`. If it matches none, the response is `not_found`. If fewer than two structural steps are supplied, the response is `insufficient_input`. In all three cases OpenWorkGraph returns bounded privacy-safe candidate families and does not invent task context.

Arbitrary prompt text, task descriptions, customer content, document text, message bodies, tool arguments, and model outputs are not inputs to the resolver.

## Example REST calls

Exact family:

```text
GET /v1/task-context?family_key=human:email.reply
Authorization: Bearer <normal OpenWorkGraph API token>
```

Canonical human family:

```text
GET /v1/task-context?task_family=email.reply
Authorization: Bearer <normal OpenWorkGraph API token>
```

Structural prefix:

```text
GET /v1/task-context?current_steps=tool:search:tool:012345abcdef,approval_request
Authorization: Bearer <normal OpenWorkGraph API token>
```

Structural tokens must be tokens that OpenWorkGraph itself can generate. Free-form text is rejected rather than normalized into a procedural step.

## Response shape

A resolved response has the following high-level form:

```json
{
  "schema_version": "1.0",
  "resolution": {
    "status": "resolved",
    "mode": "explicit_family_key",
    "family_key": "human:email.reply"
  },
  "context_available": true,
  "task_context": {
    "family_key": "human:email.reply",
    "policy": {
      "status": "active",
      "authority_class": "declared_normative",
      "authoritative_as_declared_input": true,
      "item": {}
    },
    "observed_procedure": {
      "authority_class": "observed_evidence",
      "authoritative": false,
      "similar_runs": [],
      "failure_patterns": [],
      "next_observed_steps": [],
      "approval_patterns": []
    },
    "policy_observation_comparison": null,
    "provenance": {}
  },
  "read_only": true,
  "writes_performed": false,
  "automatic_context_injection": false
}
```

The observational section preserves the existing hard caps on similar runs, pattern sections, structural steps, and evidence references.

## Authentication

`/v1/task-context` uses the normal local read credential. The write-only agent-ingest token cannot read it. This matches procedural-memory and declared-policy read surfaces.

MCP access also remains behind the existing per-run **AI access** switch and MCP audit trail. `get_task_context` uses the same secure local REST transport as the other OpenWorkGraph MCP reads.

## Privacy

Task context is assembled from the privacy-hardened canonical evidence store and the declared-policy manifest. It does not expose native run/session/trace/span IDs. Custom agent/workflow/tool identifiers remain hashed where procedural memory already hashes them. Evidence references are bounded.

The resolver does not echo selector input and does not accept a free-text task-description field. This avoids introducing a new prompt-injection/content-retrieval surface merely to select a procedural family.

## What this does not do

Task context does not:

- automatically inject itself into prompts;
- execute suggested next steps;
- approve actions;
- block actions;
- turn repeated behavior into policy;
- sign or activate policy;
- claim policy compliance from incomplete observation;
- infer success from human completion;
- persist a second learned-memory database.

Agents decide when to request context, and downstream systems remain responsible for their own execution and authorization controls.
