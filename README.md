# OpenWorkGraph — Workflow Observer v15

OpenWorkGraph is a local-first operational data layer for understanding how work actually happens. The current capture component, Workflow Observer, builds a machine-queryable evidence stream from desktop and browser activity. It is deliberately model-agnostic: capture and structure operational telemetry first, then expose it through REST/MCP so Claude, ChatGPT, or another enterprise AI can reason over it.

## What v15 captures

- application and active-window/tab focus spans (unchanged polling is not stored)
- cross-browser tab/title changes at the desktop level
- foreground, engaged, probable-idle and active-input timing
- aggregate keypress counts only — never key identities, key order, or typed text
- global mouse clicks and throttled scrolls
- best-effort macOS Accessibility labels for clicked controls
- optional browser semantic events from a WebExtension: tab activation/navigation, interactive clicks, editor/input focus, form submits, control changes, and copy/paste occurrence
- **candidate task executions** inferred from contiguous evidence, with duration, effort, work surfaces and semantic actions
- **repeated task patterns** that combine frequency with observed engaged effort

## What it deliberately does not capture

- raw keystrokes or text typed into form fields
- password/secure-field values
- clipboard contents
- URL query strings or fragments from the browser sensor
- screenshots by default

## Task inference in v15

Task inference is intentionally conservative and heuristic. A candidate task is built from evidence in one session and separated by long evidence gaps, explicit completion-like semantic actions (for example a visible `Create`, `Submit`, `Send`, or `Approve` control), or a maximum-duration guard.

Suggested task labels reuse observed work-surface/UI wording. They are **not claims about employee intent or business outcome** and always carry `needs_review: true` plus low/medium confidence.

Examples of the intended output:

- `GitHub — Create repository` — 1m 42s elapsed, 1m 28s engaged, 74 keys, 9 clicks
- `Gmail — Send` — 2m 10s elapsed, 1m 57s engaged, 183 keys
- repeated pattern: `Google Sheets → Salesforce → Outlook`, observed 11 times, 38m total engaged

The point is to give downstream AI bounded, evidence-backed units rather than an undifferentiated event firehose.

## Browser coverage

The desktop observer is the universal fallback. The optional `browser_extension` uses cross-browser WebExtension APIs. Chromium-family browsers and Firefox can load the same source for testing. Safari continues to receive app/tab + macOS Accessibility evidence until the WebExtension is packaged/signed inside the native app.

## Run on macOS

Use the standalone launcher supplied in the release zip. It installs into:

`~/Library/Application Support/WorkflowObserver`

The dashboard runs at:

`http://127.0.0.1:8787`

### Optional browser semantic sensor

With Workflow Observer running, run `OPEN_BROWSER_SENSOR_FOLDER.command` from the release zip.

For Chrome/Edge/Brave/Vivaldi/Opera/Chromium: open the browser Extensions page, enable Developer mode, choose **Load unpacked**, and select the opened `browser_extension` folder.

For Firefox: open `about:debugging#/runtime/this-firefox`, choose **Load Temporary Add-on**, and select `manifest.json` in that folder.

Reload an already-installed test extension after upgrading versions.

## API

- `GET /v1/summary?scope=current`
- `GET /v1/tasks?scope=current`
- `GET /v1/events`
- `GET /v1/semantic-activity`
- `GET /v1/sessions/{session_id}`
- `POST /v1/events`
- `POST /v1/browser-events`

## MCP

The MCP server exposes workflow summary/search/session tools, recent semantic activity, candidate task executions, and automation-candidate retrieval. The intended pattern is progressive retrieval: the AI asks for task/pattern summaries, then drills into evidence only when needed.
