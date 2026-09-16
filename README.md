# OpenWorkGraph — Workflow Observer v0.32

OpenWorkGraph is a local-first work-telemetry and organizational-context layer. Workflow Observer captures desktop/browser activity, timing, aggregate effort, navigation and semantic interaction evidence, preserves the customer-owned evidence locally, and exposes useful context and process structure through REST/MCP so ChatGPT, Claude or another enterprise AI can reason about how work actually happens.

## Design principle

Capture fidelity and privacy policy are separate concerns. OpenWorkGraph does not destroy the source evidence in order to create a safer analytics view. Each incoming event can produce three local representations:

1. **Raw local evidence** — the richest customer-owned source of truth for reconstruction and verification.
2. **Customer context** — searchable organizational memory that can retain useful visible resource titles, sanitized host/path context and UI labels while never adding typed field values or clipboard contents.
3. **Normalized operational events** — a content-minimized representation for broad process/effort analytics and task inference.

This allows an enterprise AI to use the layer appropriate to the job. A workflow-mining query can use normalized telemetry; an authorized assistant trying to find prior work on a customer/project can use the context layer; raw evidence stays local unless deliberately exported.

## What capture includes

- application and active-window/tab focus spans
- browser tab activation and navigation across major desktop browsers
- immediate browser navigation signals from `tabs` and `webNavigation`, including brief address-bar visits that require no scrolling/clicking
- foreground, engaged, probable-idle and active-input timing
- aggregate keypress counts only — never key identities, key order or typed text
- global mouse clicks and throttled scrolls
- best-effort native macOS Accessibility labels for clicked controls
- WebExtension semantic events: interactive clicks, editor/input focus, form submits, control changes and copy/paste occurrence
- candidate task executions and stable task families
- repeated completed tasks, separated from mere repeated navigation fragments

## Reliable delivery

Desktop capture is written locally before upload and placed into a SQLite outbox. If the local API is unavailable, events remain queued and are retried with the same event ID.

The browser sensor has its own durable extension-storage queue. Browser events carry stable sensor identity and capture-time work-session identity so an event queued during one observer session cannot later be silently attributed to another session. The dashboard also compares the installed browser-sensor version with the server build and warns when the unpacked extension needs to be reloaded.

## Identity model

Events use a versioned identity envelope with:

- `schema_version`
- optional `organization_id`
- optional `actor_id`
- stable `device_id`
- stable `sensor_id`
- `session_id`
- `source`

A local installation ID is persisted so restarts do not create a new device/sensor identity. Organization and actor IDs can be supplied through `config.json` for later multi-user deployments.

## Task inference

Task inference runs on normalized operational events rather than arbitrary raw titles. This reduces accidental dependence on email subjects, recipients, document names or record IDs.

For example, different emails can normalize to the same family:

`Gmail → Compose → typing effort → Send → email.compose_send`

Repeated task families remain observational evidence, not a claim that a task is safe to automate.

## REST data layers

Raw local evidence:

- `GET /v1/summary?scope=current`
- `GET /v1/events`
- `GET /v1/semantic-activity`
- `GET /v1/sessions/{session_id}`

Customer-owned context:

- `GET /v1/context-events?query=...`
- `GET /v1/context-sessions/{session_id}`

Content-minimized operational telemetry:

- `GET /v1/operational-summary?scope=current`
- `GET /v1/operational-events`
- `GET /v1/operational-semantic-activity`
- `GET /v1/operational-sessions/{session_id}`
- `GET /v1/tasks?scope=current`

Sensor coordination:

- `GET /v1/browser-context`
- `POST /v1/events`
- `POST /v1/browser-events`
- `POST /v1/heartbeat`
- `POST /v1/browser-heartbeat`

## MCP

The MCP server now supports both organizational-memory and process-analysis use cases.

Context tools include:

- `get_current_work_context`
- `search_work_history`
- `find_similar_work`
- `get_context_session`
- `find_process_examples`

Operational tools remain available for workflow summaries, normalized observation search, task executions, session traces and automation candidates. Raw evidence is not exposed through MCP by default.

## Browser navigation accuracy

The browser extension uses multiple independent signals so simply opening a new tab, typing a URL and pressing Enter can be observed without waiting for a later page interaction:

- `tabs.onUpdated`
- `webNavigation.onBeforeNavigate`
- `webNavigation.onCommitted`
- `webNavigation.onCompleted`
- document-start page observation
- History API route changes for SPAs

Failed browser-event delivery is queued locally and retried. After upgrading OpenWorkGraph, reload the unpacked extension; the dashboard reports an explicit version mismatch if an old extension is still running.

## Windows support

Windows is supported for the core observer:

- foreground application/title through Win32 APIs
- process identity through `psutil`
- aggregate keyboard activity through `pynput`
- click/scroll capture through `pynput`
- screenshots through `mss`
- Chrome/Edge/Firefox-family browser semantic capture through the WebExtension
- local API, dashboard, exports and MCP

Native desktop control labels currently use macOS Accessibility APIs, so arbitrary native Windows applications have less semantic control detail than macOS. Browser applications retain semantic page/control evidence through the extension. The repository CI runs the Python test suite on Windows, macOS and Linux.

Run `START_ON_WINDOWS.bat` on Windows. It reconciles dependencies on every launch so upgrades pick up new Windows capture dependencies.

## macOS

Run the macOS launcher and approve Accessibility/Input Monitoring when requested. The local dashboard is:

`http://127.0.0.1:8787`

Reload `browser_extension` after upgrading builds.

## Exports

The dashboard exports the captured run as JSON, XLSX or a ZIP of CSV tables. The content-minimized operational representation is available for safer analysis, while **Include rich raw session evidence** deliberately includes the richer customer-owned evidence for models/tools that need full reconstruction.

## Prototype security boundary

The current server binds to `127.0.0.1`. Before centralized enterprise deployment, raw/context access should be governed by authentication, encryption, RBAC, retention policy, audit logs and organization-specific collection policy. The current work is deliberately focused on getting the local evidence/context protocol correct before adding a cloud control plane.
