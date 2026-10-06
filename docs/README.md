# OpenWorkGraph documentation

The repository root is intentionally small: [README](../README.md) explains the product, [SPEC](../SPEC.md) defines the stable semantic contract, and [AGENTS](../AGENTS.md) tells capable AI agents how to install/connect/use OWG.

Everything more detailed belongs here.

## AI and connected agents

- [AI guide](ai/guide.md) — how an AI should interpret OWG evidence and distinguish observation, heuristics, inference, and user-confirmed rules.
- [Automation capability brief](ai/automation-capabilities.md) — evaluate modern automation feasibility without assuming only legacy RPA/macros exist.
- [Starter prompt](ai/starter-prompt.md) — compact instruction for analyzing an exported evidence package.
- [Conversational clients](CONVERSATIONAL_CLIENTS.md) — ChatGPT/Microsoft 365 Copilot/background capture patterns.
- [MCP architecture](MCP_ARCHITECTURE.md) — local stdio MCP, compatibility surfaces, HTTP/Gateway MCP and authentication.
- [Agent evidence contract](AGENT_EVIDENCE.md) — structural telemetry for agent runs, tools, handoffs and approvals.
- [Native agent adapters](NATIVE_AGENT_ADAPTERS.md) — Claude Code, Codex/OTLP and generic adapters.
- [OpenAI Agents SDK adapter](OPENAI_AGENTS_ADAPTER.md) — additive Python tracing adapter.
- [Agent context handoff](AGENT_CONTEXT_HANDOFF.md), [execution traces](AGENT_EXECUTION_TRACES.md), and [Gateway sharing](AGENT_GATEWAY_SHARING.md).

## Evidence, continuity and workflow context

- [Context pulse](CONTEXT_PULSE.md)
- [Task context](TASK_CONTEXT.md)
- [Task preflight](TASK_PREFLIGHT.md)
- [Context/execution linkage](CONTEXT_EXECUTION_LINKAGE.md)
- [Context/handoff delivery](CONTEXT_HANDOFF_DELIVERY.md)
- [Context/outcome associations](CONTEXT_OUTCOME_ASSOCIATIONS.md)
- [Procedural memory](PROCEDURAL_MEMORY.md)
- [Procedural context packs](PROCEDURAL_CONTEXT_PACK.md)
- [Discovery Mode](DISCOVERY_MODE.md)
- [Exports and AI](EXPORTS_AND_AI.md)

## Privacy and data

- [Privacy and data handling](PRIVACY_AND_DATA.md)
- [Data lifecycle](DATA_LIFECYCLE.md)
- [Owner/person redaction](OWNER_REDACTION.md)
- [Declared policy](DECLARED_POLICY.md)

Sweden-specific deployment/compliance material is under [compliance/sweden/](compliance/sweden/README.md).

## Gateway, organizations and deployment

- [Self-hosting](SELF_HOSTING.md)
- [Organization rollout](ORGANIZATION_ROLLOUT.md)
- [Human access / OIDC](HUMAN_ACCESS.md)
- [Gateway hardening](GATEWAY_HARDENING.md)
- [Integrations](INTEGRATIONS.md)
- [Enterprise policy signing](ENTERPRISE_POLICY_SIGNING.md)
- [Signed releases](SIGNED_RELEASES.md)

The fleet/deployment artifacts themselves live under [../platform/enterprise/](../platform/enterprise/README.md).

## Testing and packaging

- [Nontechnical testing](testing/nontechnical.md)
- [Standalone macOS launcher](STANDALONE_MAC_LAUNCHER.md)
- [Custom harnesses](CUSTOM_HARNESSES.md)

## Experimental governance

The default compact MCP surface is evidence/context focused. Governance features remain implemented and tested but are intentionally secondary/experimental.

- [Experimental governance](EXPERIMENTAL_GOVERNANCE.md)
- [Policy action advisory](POLICY_ACTION_ADVISORY.md)
- [Policy approval gate](POLICY_APPROVAL_GATE.md)
- [Policy-bound approval receipts](POLICY_BOUND_APPROVAL_RECEIPTS.md)
- [Policy authoring](POLICY_AUTHORING.md)
- [Policy source sync](POLICY_SOURCE_SYNC.md)
- [Policy source drift](POLICY_SOURCE_DRIFT.md)
- [Shadow-enforcement preview](SHADOW_ENFORCEMENT_PREVIEW.md)
- [Shadow-enforcement outcome analytics](SHADOW_ENFORCEMENT_OUTCOME_ANALYTICS.md)

## Historical implementation notes

Version-specific files such as `V035_*` and `V058_*` are retained for regression/development traceability. They are not the current product contract. Use [SPEC.md](../SPEC.md), [README.md](../README.md), and the current topic docs above for present behavior.
