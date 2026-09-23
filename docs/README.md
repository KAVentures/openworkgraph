# OpenWorkGraph documentation

This directory contains implementation, privacy, self-hosting and integration notes for the current OpenWorkGraph prototype plus a small number of historical version-specific notes kept for traceability.

## Current documentation

- [Self-hosting the organization Gateway](SELF_HOSTING.md) — run the optional Gateway/PostgreSQL data plane entirely in customer-controlled infrastructure, enroll endpoints, manage sharing policy, and create scoped integration credentials.
- [Gateway operational hardening](GATEWAY_HARDENING.md) — opt-in PostgreSQL pooling, credential-scoped rate limiting, non-secret token/device inventory, administrative device revocation, and runtime visibility.
- [Gateway data lifecycle](DATA_LIFECYCLE.md) — opt-in organization retention, dry-run-first physical cleanup, retroactive actor/device/session/time-scoped evidence purge, audit behavior, and backup caveats.
- [How MCP works](MCP_ARCHITECTURE.md) — plain-language explanation of local stdio MCP, on-demand local HTTP MCP, organization Gateway MCP, authentication, and why MCP does not imply cloud storage.
- [Integrations](INTEGRATIONS.md) — vendor-neutral REST/MCP patterns for automation systems, company brains and other AI platforms.
- [Privacy and data handling](PRIVACY_AND_DATA.md) — what is captured, what is deliberately not captured, storage-time sanitization, presentation redaction, retained context, and local trust boundaries.
- [Exports and AI analysis](EXPORTS_AND_AI.md) — JSON/XLSX/CSV ZIP behavior, the AI data dictionary, semantic columns, privacy tokens, and recommended upload workflow for ChatGPT/Claude.
- [Owner/person presentation redaction](OWNER_REDACTION.md) — how `OWNER`, `PERSON`, learned aliases and the reset control work.
- [Standalone macOS launcher](STANDALONE_MAC_LAUNCHER.md) — packaging/launcher implementation notes.
- [Nontechnical testing guide](../NONTECHNICAL_TESTING.md) — practical end-to-end checks for the current release.

The repository root [README](../README.md) is the canonical product overview and installation guide.

## Historical notes

The following files describe older implementation milestones and should not be treated as current product documentation:

- `CHANGELOG_V035.md`
- `V035_NOTES.md`
- `V035_TEST_CASES.md`

They remain in the repository to preserve development history and old regression rationale.
