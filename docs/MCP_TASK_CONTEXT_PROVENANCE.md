# MCP task-context representation provenance

The secure local MCP already exposes `get_task_context(...)` as a read-only way for an authorized AI client to retrieve OpenWorkGraph organizational context.

MCP has an additional trust boundary that direct local API preflight does not: observed text is passed through OpenWorkGraph's prompt-injection protection before it is returned to the MCP client. Instruction-like observed strings may therefore be suppressed or normalized in the MCP-visible copy while the canonical local evidence and `/v1/task-context` source response remain unchanged.

OpenWorkGraph now makes those two representations explicit instead of pretending they are byte-for-byte identical.

## Returned provenance

`get_task_context(...)` adds a trusted top-level object:

```text
_openworkgraph_task_context_provenance
```

It contains:

- `source_api_snapshot_sha256` — SHA-256 of the exact local `/v1/task-context` JSON object before MCP protection, using the same canonical JSON hashing as `TaskPreflightClient`;
- `mcp_protected_snapshot_sha256` — SHA-256 of every MCP-visible protected result field before the provenance object itself is appended;
- the fingerprint scope description;
- `prompt_injection_protection_applied = true`;
- `mcp_tool_result_emitted = true`;
- `mcp_client_receipt_attested = false`;
- `model_context_consumption_attested = false`;
- `model_context_use_attested = false`;
- `automatic_context_injection = false`.

The existing `_openworkgraph_security` object remains present and continues to report suppressed/normalized observed fields.

## Why there are two fingerprints

A direct `TaskPreflightClient` fingerprints the exact `/v1/task-context` source response.

An MCP client can receive a different safe representation because the MCP boundary may replace instruction-like observed text with suppression markers and always adds security metadata. Therefore:

```text
source API task context
        |
        | source_api_snapshot_sha256
        v
MCP prompt-injection protection
        |
        | mcp_protected_snapshot_sha256
        v
MCP-visible task context + trusted provenance metadata
```

The source fingerprint establishes which local task-context response was the input to the MCP boundary. The protected fingerprint establishes which protected context body was presented by the tool, excluding only the provenance object that contains the fingerprint itself.

## Delivery boundary

`mcp_tool_result_emitted = true` means the OWG MCP tool produced the result through its normal return path. It does **not** prove that:

- the MCP client acknowledged receipt;
- a model read the tool result;
- a model attended to, remembered, or used the context;
- the context caused later actions or outcomes;
- the model complied with policy.

This is intentionally more conservative than a runtime integration explicitly asserting `delivered_to_runtime` through the separate context-delivery helper.

## Security and auditing

The task-context MCP path still uses the existing AI-access authorization gate.

The source response is never returned before protection. The MCP-visible copy is produced by the same `protect_observed_payload(...)` function used by other MCP tools. Trusted provenance is appended only after that protection step, then the complete final result — including provenance — is passed through the existing MCP activity audit hook.

No canonical evidence, database rows, declared policy, or task-context source object are modified by this process.

## Compatibility

All non-task-context MCP tools keep the existing generic finish path unchanged.

The task-context response keeps all existing top-level task-context fields; the provenance object is additive. There is no automatic prompt injection, automatic execution, policy enforcement, or native agent adapter activation in this feature.
