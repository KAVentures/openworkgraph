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
- [Agent-ready workflow maps](AGENT_WORKFLOW_MAP.md) — map observed work, feed existing agents, execute with their tools, verify outcomes.
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

## Process mining on event logs

- [Event-log workspace](EVENT_LOG_WORKSPACE.md): import CSV, Excel, SQLite, JSON Lines, Parquet, XES and OCEL 2.0 logs, then analyse maps, variants, KPIs, conformance, simulation and predictions. It starts with a [formats table](EVENT_LOG_WORKSPACE.md#formats).
- Data quality: [import and append quality](EVENT_LOG_QUALITY_TRENDS.md), [quarantined rows](EVENT_LOG_QUARANTINE_PREVIEW.md) and [grouping activities](EVENT_LOG_ACTIVITY_MAPPINGS.md).
- Cases needing attention: [case evidence](EVENT_LOG_CASE_EVIDENCE.md), [stuck open cases](EVENT_LOG_STUCK_CASES.md), [rework and loops](EVENT_LOG_REWORK.md) and [signals](EVENT_LOG_SIGNAL_OVERVIEW.md).
- Time and capacity: [work in progress](EVENT_LOG_WORK_IN_PROGRESS.md), [when work happens](EVENT_LOG_TIME_PATTERNS.md), [case durations](EVENT_LOG_DURATION_DISTRIBUTION.md), [batch processing](EVENT_LOG_BATCHING.md) and [simulation alternatives](EVENT_LOG_SIMULATION_PORTFOLIO.md).
- Change and outcomes: [intervention impact](INTERVENTION_IMPACT.md).
- [Object-centric process models](OBJECT_CENTRIC_PROCESS_MODELS.md).
- Process designs: [process catalog](PROCESS_CATALOG.md), [BPMN import](PROCESS_BPMN_IMPORT.md) and [portable BPMN diagrams](PROCESS_DIAGRAM_INTERCHANGE.md).
- [Domain packs](DOMAIN_PACKS.md): reusable, versioned process definitions such as order to cash, procure to pay and IT service management.
- [Process intelligence for AI agents](PROCESS_INTELLIGENCE_FOR_AGENTS.md): what shared agents may read from a log.
- [Competitor gap assessment](COMPETITOR_GAP_ASSESSMENT_2026_10.md): what remains to be built.

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
