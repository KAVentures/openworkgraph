# OpenWorkGraph documentation

This directory contains implementation, privacy, self-hosting and integration notes for the current OpenWorkGraph prototype plus a small number of historical version-specific notes kept for traceability.

## Current documentation

- [Self-hosting the organization Gateway](SELF_HOSTING.md) — run the optional Gateway/PostgreSQL data plane entirely in customer-controlled infrastructure, enroll endpoints, manage sharing policy, and create scoped integration credentials.
- [Human access, OIDC and privacy-scoped reads](HUMAN_ACCESS.md) — optional SSO for people, self/team/org authorization, thresholded aggregate-only access, and explicitly scoped pseudonymous reads while preserving machine-token compatibility.
- [Gateway operational hardening](GATEWAY_HARDENING.md) — opt-in PostgreSQL pooling, credential-scoped rate limiting, non-secret token/device inventory, administrative device revocation, and runtime visibility.
- [Gateway data lifecycle](DATA_LIFECYCLE.md) — opt-in organization retention, dry-run-first physical cleanup, retroactive actor/device/session/time-scoped evidence purge, audit behavior, and backup caveats.
- [How MCP works](MCP_ARCHITECTURE.md) — local compact stdio MCP for new connections, the legacy 24-tool compatibility entrypoint, on-demand local HTTP MCP, organization Gateway MCP, authentication, and why MCP does not imply cloud storage.
- [Integrations](INTEGRATIONS.md) — vendor-neutral REST/MCP patterns for automation systems, company brains and other AI platforms.
- [Agent evidence contract](AGENT_EVIDENCE.md) — vendor-neutral structural telemetry for agent runs, tool calls, trace/span relationships, human approvals, observation depth, cross-actor workflow correlation, and privacy-safe adapter design. Exact standard OTLP exporter environment variables are documented in [Native agent adapters](NATIVE_AGENT_ADAPTERS.md#generic-opentelemetry-trace-export).
- [Native agent adapters](NATIVE_AGENT_ADAPTERS.md) — opt-in Claude Code lifecycle hooks, Codex OTLP trace ingestion and exact generic OTLP/HTTP JSON exporter configuration, with least-privilege credentials, fail-open behavior and privacy boundaries.
- [OpenAI Agents SDK adapter](OPENAI_AGENTS_ADAPTER.md) — additive tracing processor, bounded fail-open delivery, hashed native IDs and strict structural privacy projection for Python agent applications.
- [Procedural memory](PROCEDURAL_MEMORY.md) — read-only evidence-backed workflow families, similar runs, explicit failure patterns, observed next steps and approval hotspots across humans and agents.
- [Procedural context packs](PROCEDURAL_CONTEXT_PACK.md) — hard-capped observational workflow-memory bundles for agents, with explicit policy/authority separation and no automatic injection or execution.
- [Unified task context](TASK_CONTEXT.md) — one read-only REST/MCP surface that resolves a procedural family conservatively and returns declared policy, observed procedure, comparison results and provenance without conflating their authority.
- [Task-context preflight client](TASK_PREFLIGHT.md) — reusable non-prescriptive Python client for pre-action context retrieval, explicit fail-open availability semantics, stable context fingerprints and no automatic prompt injection or enforcement.
- [Context to execution linkage](CONTEXT_EXECUTION_LINKAGE.md) — privacy-safe linkage from a preflight context/policy fingerprint to the structural agent run and observed outcome, with no prompt/reasoning capture and no causal claim.
- [Context/outcome associations](CONTEXT_OUTCOME_ASSOCIATIONS.md) — aggregate same-family/same-observation-depth outcome comparisons with minimum-support gates, explicit unknown outcomes and no causal/effect claim.
- [Privacy and data handling](PRIVACY_AND_DATA.md) — what is captured, what is deliberately not captured, storage-time sanitization, presentation redaction, retained context, and local trust boundaries.
- [Exports and AI analysis](EXPORTS_AND_AI.md) — JSON/XLSX/CSV ZIP behavior, the AI data dictionary, semantic columns, privacy tokens, and recommended upload workflow for AI analysis.
- [Owner/person presentation redaction](OWNER_REDACTION.md) — how `OWNER`, `PERSON`, learned aliases and the reset control work.
- [Standalone macOS launcher](STANDALONE_MAC_LAUNCHER.md) — packaging/launcher implementation notes.
- [Nontechnical testing guide](../NONTECHNICAL_TESTING.md) — practical end-to-end checks for the current release.

The repository root [README](../README.md) is the canonical product overview and installation guide.

## Experimental governance

The default compact MCP surface is evidence/context focused. Normative governance remains implemented and tested, but is intentionally positioned as experimental rather than as the primary product workflow. See [Experimental governance surfaces](EXPERIMENTAL_GOVERNANCE.md) for the `OWG_EXPERIMENTAL_GOVERNANCE` exposure flag and compatibility model.

These existing documents remain at their current paths so old links do not break:

- [Declared policy action advisory](POLICY_ACTION_ADVISORY.md)
- [Opt-in policy approval gate](POLICY_APPROVAL_GATE.md)
- [Policy-bound approval receipts](POLICY_BOUND_APPROVAL_RECEIPTS.md)
- [Declared policy / SOP plane](DECLARED_POLICY.md)
- [Controlled policy authoring](POLICY_AUTHORING.md)
- [Trusted policy source sync](POLICY_SOURCE_SYNC.md)
- [Policy source drift and provenance](POLICY_SOURCE_DRIFT.md)
- [Enterprise policy signing and distribution](ENTERPRISE_POLICY_SIGNING.md)
- [Shadow-enforcement preview](SHADOW_ENFORCEMENT_PREVIEW.md)
- [Shadow-enforcement outcome analytics](SHADOW_ENFORCEMENT_OUTCOME_ANALYTICS.md)

The underlying REST routes remain available by default for compatibility. The experimental flag controls compact MCP exposure; it does not delete or unmount governance code.

## Historical notes

The following files describe older implementation milestones and should not be treated as current product documentation:

- `CHANGELOG_V035.md`
- `V035_NOTES.md`
- `V035_TEST_CASES.md`

They remain in the repository to preserve development history and old regression rationale.