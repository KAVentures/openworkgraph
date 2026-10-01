# OpenWorkGraph v0.112.0

## Trustworthy AI answers and agent-run accounting

v0.112.0 hardens the accuracy of the evidence OpenWorkGraph returns to connected AI while keeping the existing local-first, evidence-first privacy model intact.

### One physical Claude Code session stays one execution

Claude Code hook/OTel observations and native-session fallback now use the same **opaque** cross-sensor session identity. Top-level prompt turns remain observations inside the physical session instead of becoming separate executions, while genuine subagents retain separate opaque child execution identities.

Tool calls observed by both hooks and transcript fallback share an opaque tool identity as well. This lets OpenWorkGraph prefer the richer hook/OTel observation even when the transcript timestamp differs, without collapsing legitimate adjacent calls merely because they used the same tool.

Raw native session, prompt-turn, tool-use, trace and span identifiers are not exposed. Existing retained rows created before v0.112 are not silently rewritten; newly observed evidence uses the corrected identity scheme.

### Test outcomes stay passing, failing or unknown

Agent-run summaries distinguish known passing test runs, known failing test runs and runs whose result is **unknown**. Missing result evidence is never represented as zero failures.

When an otherwise-unknown run has observed test evidence, the latest observed test result can resolve the run outcome: a final passing run may resolve to success and a final failing run may resolve to error, while earlier observed failures remain counted in the run summary.

### Token usage is observed evidence, never an estimate

**Token usage** is reported only when provider, SDK or compatible telemetry exposes exact usage counters. Runs with such evidence report token usage as `observed` with the recorded counts.

When the observation surface does not expose token usage, OpenWorkGraph reports `not_observed` with a basis and empty counts. It does not estimate tokens from visible text, elapsed time, screen activity or model identity, and it never treats an unobserved token signal as zero.

### Smaller default MCP evidence pages

`get_workflow_trace` now returns compact rows by default with a smaller default page size while preserving stable pagination and canonical chronology. Connected AI can explicitly request `detail="rich"` for the full authorized row.

For questions covering a whole period, models should continue following `next_cursor` until `has_more` is false. Compact mode changes payload size, not the underlying evidence or retention/access boundary.

### Better default scope after restart

`get_work_profile(scope="current")` and the compact current-work overview can fall back to authorized evidence from **local today** when the current launcher session is empty after a restart. Responses disclose the requested and actually used scope and include a hint explaining the fallback.

Saved-history access remains a separate permission. If the connected AI lacks the required access, OpenWorkGraph explains that instead of silently broadening the scope.

### More readable repeated workflows

Repeated-workflow candidates now expose readable typical steps and use those steps for the display label rather than relying only on a guessed task label.

Engaged-time and foreground-time evidence remain distinct. When engaged time is zero or unobserved but foreground duration exists, the response may expose an explicit foreground-time fallback while preserving the original engaged and foreground metrics and identifying the duration basis.

### Clearer saved-history remediation

Aggregate MCP tools that require all saved history now name the exact remedy: grant **All saved history** in History, or use `get_workflow_trace` with `since`/`until` inside the already granted date range when that satisfies the question.

### Native session sensor health

Clean bootstrap and cleanup paths no longer increment the native-session sensor error counter. Real failures increment the counter and expose only a bounded `last_error` stage plus exception type; local paths, provider payloads and content are not included.

### Privacy and compatibility boundaries remain intact

- The compact MCP surface remains 12 tools.
- Canonical workflow evidence remains the source of truth; derived summaries remain non-authoritative.
- Hidden reasoning is not captured or exposed.
- Ordinary **raw tool output** is not persisted by this accuracy work.
- Arbitrary shell arguments, typed text and clipboard contents are not added to canonical evidence.
- Existing Redacted/Full disclosure controls and saved-history authorization remain in force.
- Token values are never inferred when the runtime does not expose them.
