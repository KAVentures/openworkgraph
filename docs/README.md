# OpenWorkGraph documentation

This directory contains implementation and privacy notes for the current OpenWorkGraph prototype plus a small number of historical version-specific notes kept for traceability.

## Current documentation

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
