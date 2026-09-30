# OpenWorkGraph v0.111.0

## Provenance-safe agent Working Detail

OpenWorkGraph can now keep a small, factual continuity layer for supported local agent sessions so a later agent can understand the practical state of work without turning provider transcripts or tool output into canonical workflow evidence.

Working Detail is separate from the canonical `events` stream. It is OFF by default, has its own retention policy, and connected-AI read access is a separate OFF-by-default permission. Disabling it leaves the existing structural observer and session-continuity behavior unchanged.

### What can be retained

When explicitly enabled, Working Detail can retain bounded, privacy-hardened facts such as workspace-relative files, allowlisted command/program names, test outcomes and counts, failing test identifiers, short redacted error excerpts, and repository state such as dirty state, short commit identity, branch name, and bounded changed-file paths. Each record carries provenance and is marked non-authoritative observed context.

Test outcomes are tri-state: passing, failing, or unknown. Missing result evidence stays unknown. A zero observed-failure count is not treated as proof that a test run passed.

### What is not retained

Raw tool output is not stored. Arbitrary shell arguments, absolute workspace paths, prompts, visible conversation, provider-native records, secrets, and hidden reasoning are not Working Detail. Visible user/assistant messages remain governed by the existing separate session-message controls.

File paths must resolve inside the observed workspace and are stored workspace-relative. Traversal and outside-workspace paths are rejected. Working Detail does not broaden organization Gateway sharing in v0.111.

### Capture and import behavior

Normal enablement starts at the current end of supported local Claude Code and Codex session history, so an upgrade or first enable does not silently backfill prior sessions. An explicit historical import can be requested for a bounded recent window. Historical tool-result text is parsed in memory to derive structured facts and then discarded; raw tool output and hidden reasoning are not imported.

Working Detail scanning has an independent cursor and runtime. A Working Detail parse or storage failure is fail-open with respect to the canonical structural observer and must not block normal OpenWorkGraph capture.

### Handoff and compatibility

The existing agent handoff can include bounded Working Detail when the relevant local capture and connected-AI read permissions allow it. The compact MCP tool surface does not gain a new tool. Existing grounding keys and legacy agent-run summary shapes remain compatible.

The canonical event schema is unchanged in this release.
