# OpenWorkGraph v0.94.0

## History and retention

- Adds explicit local retention policy for human and agent evidence with separate modes: ephemeral, N days, or forever.
- New installs default to ephemeral until onboarding is completed. Existing installs preserve already-retained history on upgrade rather than deleting it silently.
- Adds a History view/API for browsing retained human sessions and agent executions, deleting one session, and exporting a selected retained time range.
- Session deletion and expiry install durable session tombstones before cleanup so buffered/retried events cannot recreate deleted history.
- Context Pulse cursors are invalidated when retained history or saved-history access changes.

## Saved-history AI access

- Retention and AI disclosure are separate controls.
- Saved history requires an explicit time-limited AI history lease (`selected_range` or `all_saved`). Current-session context continues to work without a saved-history lease.
- The MCP transport centrally intersects historical reads with the allowed date range so older tools cannot bypass the lease.
- Adds `list_history` for metadata-first navigation without eagerly loading full historical evidence.

## Universal structural agent observation

- Adds browser-surface lifecycle adapters for ChatGPT, Claude web, Microsoft Copilot, Lovable, and Gemini.
- Browser-observed agent runs are projected into the existing vendor-neutral agent evidence contract with `observation_level=os_observed`.
- The adapters emit structural lifecycle only: run started/finished/cancelled, approval requested/received, and visible error state.
- They do not capture prompts, model responses, DOM content, tool arguments/results, typed text, clipboard contents, filenames/file contents, or hidden reasoning.
- No new browser permissions are required.

## Versioning

- Root package version: `0.94.0`.
- Claude MCP bundle version: `0.94.0`.
- Browser sensor version: `1.12.0-v94-agent-lifecycle`.
