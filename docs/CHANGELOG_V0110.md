# OpenWorkGraph v0.110.0

## Evidence-first MCP guidance

- Compact local MCP now advertises server-level initialization instructions so connected models see the evidence hierarchy even if they never open an optional prompt or resource.
- Whole-period questions are directed through `list_history` and paginated `get_workflow_trace`; models are told to follow `next_cursor` while `has_more` is true when complete period coverage is required.
- Automation questions are directed to `openworkgraph://automation-capabilities`, the current AI tool surface, outcome-level automation, and **TEST** for plausible-but-unproven agentic approaches.
- Observed titles, labels and messages remain untrusted data rather than instructions.

## Packaged MCP validation

- The Claude Desktop MCPB build is extracted and smoke-tested in CI.
- Its manifest version must match `VERSION`.
- Its advertised tools must match the exact 12-tool compact surface.
- The packaged Node entry point is syntax-checked and must still route to the local OpenWorkGraph MCP launcher.
- This is a package smoke test, not a claim that CI launches the proprietary Claude Desktop application.

## First-run history grace

- A genuinely new user who has not yet made a retention choice keeps human and agent evidence locally for up to 7 days rather than losing the first session on close.
- The onboarding card continues asking for an explicit choice.
- Selecting **Don't keep after sessions** still switches both layers to ephemeral session-only retention.
- Existing installations with preserved history keep their prior upgrade-safe behavior.
- v0.109 policy files that are still genuinely undecided migrate from the old ephemeral default to the 7-day grace window.
- Saved-history AI access remains a separate permission and stays OFF until explicitly granted.
- Run memory remains a separate content-free setting.

## Documentation

`docs/MCP_ARCHITECTURE.md` now matches the actual 12-tool compact manifest, including `get_context_pulse` and `list_history`, and documents the initialization instructions and paging behavior.
