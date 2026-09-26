# Experimental governance surfaces

OpenWorkGraph's core product surface is observed human/agent workflow evidence, context retrieval, repeated-work discovery and privacy-safe execution feedback.

The repository also contains experimental declared-policy, approval-gate, policy-source/signing and shadow-enforcement work. That code is retained for research, compatibility and continued testing, but it is not part of the default compact MCP tool menu.

## Exposure flag

Set:

```bash
OWG_EXPERIMENTAL_GOVERNANCE=1
```

to add the experimental normative governance tools to the **compact MCP** server.

With the flag unset (the default), the compact MCP server exposes eight tools focused on context and evidence. With the flag enabled it additionally exposes:

```text
get_action_policy_advisory
get_governed_context_pack
```

Observed approval-request hotspots are intentionally **not** treated as governance or policy. They remain available descriptively inside `how_did_similar_runs_go` because repeated approval requests are evidence about prior executions, not an authorization rule.

## What the flag does not do

The flag does **not**:

- delete governance code;
- unmount existing REST routes;
- change declared-policy storage or signatures;
- change approval or shadow-enforcement semantics;
- infer policy from observed behavior;
- change human/agent capture;
- change Gateway synchronization;
- affect the legacy `mcp_server.secure_stdio` entrypoint used by existing saved MCP configurations.

Keeping the REST layer available avoids breaking existing integrations and research workflows. The flag is an **exposure/product-surface control**, not an API-versioning mechanism.

## Legacy versus compact MCP

Existing configurations that explicitly launch:

```text
python -m mcp_server.secure_stdio
```

continue to receive the legacy 24-tool compatibility surface.

New OpenWorkGraph connection configurations, the Claude MCP bundle and the on-demand local HTTP MCP bridge use the compact MCP surface. See [MCP architecture](MCP_ARCHITECTURE.md).

## Related experimental documents

The following topics should be read as experimental rather than as the primary product path:

- [Declared policy / SOP plane](DECLARED_POLICY.md)
- [Declared policy action advisory](POLICY_ACTION_ADVISORY.md)
- [Opt-in policy approval gate](POLICY_APPROVAL_GATE.md)
- [Policy-bound approval receipts](POLICY_BOUND_APPROVAL_RECEIPTS.md)
- [Controlled policy authoring](POLICY_AUTHORING.md)
- [Trusted policy source sync](POLICY_SOURCE_SYNC.md)
- [Policy source drift and provenance](POLICY_SOURCE_DRIFT.md)
- [Enterprise policy signing and distribution](ENTERPRISE_POLICY_SIGNING.md)
- [Shadow-enforcement preview](SHADOW_ENFORCEMENT_PREVIEW.md)
- [Shadow-enforcement outcome analytics](SHADOW_ENFORCEMENT_OUTCOME_ANALYTICS.md)

These files remain at their existing paths so old links, PRs and external references do not break.
