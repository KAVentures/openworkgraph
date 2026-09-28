# OpenWorkGraph (unreleased, planned v0.98): capture correctness

Measured on a real install, OpenWorkGraph's evidence had four problems:
- Idle time was recorded as focus time. 198 h of foreground time had 12.3 h with any input, and single spans ran overnight.
- Two collectors sometimes recorded at once. 8% of focus time was duplicated, and some days showed more than 24 h.
- About 12% of all events were "capture health" warnings caused by routine checkpoints.
- Sensors reported "ON" on macOS while the OS was withholding their input.

This release fixes those without changing what a field means, what is shared, or the event schema.

## Time you were away is no longer focus time
- **When a span ends:** a focus span ends after 5 minutes (`away_after_seconds`, minimum 60) with no keyboard or mouse input anywhere, or when the screen locks. The time until input returns becomes an **away span** (`event_type: "away_span"`, app "Away").
- **Reading time kept:** the first 5 minutes stay with the app, which covers reading and thinking without input.
- **Meanings unchanged:** `duration_seconds` still means wall-clock observed time. Effort is still `activity.engaged_seconds` / `idle_seconds`.
- **Where idle comes from:** the operating system's input clock on macOS and Windows. It needs no Input Monitoring permission and never sees keys. OpenWorkGraph's own sensors are a fallback, used only when they can actually see input.
- **No idle signal:** without one (Linux today) there is no away detection, instead of guessed absences.
- **Stays on the computer:** away spans are never sent to an organization Gateway, whatever its policy. Sharing them would make OpenWorkGraph presence monitoring.

## One collector per data folder
- **The lock:** the collector holds an OS advisory lock on its data folder for its lifetime. The OS releases it on exit or crash, so there is no stale PID file.
- **A second collector:** another one for the same folder (an orphan from an earlier launch, or a direct launch) exits with code 75 and records nothing. The supervisor checks again every 15 s instead of respawning every second.
- **Separate folders:** `data/live` and `data/demo` are separate.

## Document changes within one app
- **The new default:** `change_detection: "application_and_document"`. When the window title changes materially within the same app and stays changed for 2 polls, the span ends. The new span starts when the new document first appeared.
- **What counts as noise:** unread badges, unsaved markers, "Edited"/"Redigerad", progress percentages and "Not Responding" are ignored, as are one-poll dialogs and titles going empty.
- **Title privacy modes:**
  - `none`: nothing about titles is used.
  - `hash`: normalization happens in memory before hashing, and nothing new is stored.
  - Excluded apps: never tracked.
- **Existing configs:** `"application"` was the shipped default copied into every `config.json`, so it now follows the default. `"application_only"` keeps the old behaviour. `"application_and_title"` is unchanged.

## Capture health means degradation again
- **What triggers a warning:** only real degradation produces `capture_health` evidence, namely dropped interactions, worker errors, an unreadable foreground window, or missing permissions. A new kind of problem or a permission change is reported immediately; repeats at most every 10 minutes.
- **Routine counters:** checkpoints, capture gaps, document boundaries and away spans go to `diagnostics` in the heartbeat and event instead.

## macOS permissions are stated, not assumed
- **How it checks:** the collector asks macOS directly with `AXIsProcessTrusted` and `IOHIDCheckAccess`, which never prompt, and rechecks every minute.
- **What it reports:** it names each sensor that is BLOCKED, and why. Clicks and titles need Accessibility; keyboard counts and copy/paste shortcuts need Accessibility and Input Monitoring.
- **Heartbeat:** `keyboard_sensor` is false when keys cannot arrive, and the permissions are included.
- **Dashboard:** the Recording pill shows "macOS … permission missing". Missing permissions are recorded as capture health.

## Claude Code turns
- **New hooks:** `UserPromptSubmit` starts a turn and `Stop` finishes it successfully, keyed by `prompt_id`, which Claude's tool hooks and OTel share. Sessions keep their own `SessionStart`/`SessionEnd` boundary.
- **Missing `prompt_id`:** those turn hooks are ignored rather than mistaken for the session.
- **Content never read:** these hooks carry the prompt and the last reply, and the adapter reads neither.
- **Existing installs:** OpenWorkGraph adds missing hook events at startup when Claude Code Observe is on, with a backup. Your own hooks and env are kept, and hooks are never installed where Observe was not turned on.

## Agent telemetry diagnostics
- **Where:** `GET /v1/agent-telemetry/diagnostics`, and a line in each Connections row.
- **What it shows:** per channel (Claude hooks, Claude OTel logs, Codex OTel, other agent events, generic OTel, spool), since OpenWorkGraph started:
  - when requests arrived;
  - rejections by reason;
  - OTel records seen and ignored;
  - events stored.

  It also shows which exporters and hook events are configured.
- **No content:** counts, timestamps and reason codes only.

## Delayed delivery for agent hooks, only while recording
- **When it spools:** if a hook cannot reach OpenWorkGraph, the event may wait in a local spool (`data/auth/agent_spool`) under a 90-second recording lease. Only a running, recording, non-demo OpenWorkGraph grants the lease, and it is revoked on Pause, Stop and exit.
- **After Stop or quit:** agent events are dropped, not collected for later.
- **On delivery:** spooled events go through the normal ingest rules again (Observe switches, deletions, retention, pause windows).
- **Limits:** 2,000 files of 256 KB, 24 h maximum age. A rejection is never spooled.

## Not in this release
- Clipboard-write detection.
- Copilot, Gemini CLI and Cursor presets.
- Multilingual web-agent detection.
- Claude metrics export.
- `PreCompact` (it needs a schema operation that older Gateways would reject).
- Platform rework: macOS without `osascript`, Windows UWP app names, Linux/Wayland.
- **Known issue, unchanged:** Claude subagent runs share their parent turn's `run_id`, so they are grouped into the parent turn rather than linked as children.
