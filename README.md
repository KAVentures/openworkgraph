# OpenWorkGraph

**A local-first context layer for how work actually happens.**

OpenWorkGraph observes desktop and browser work, preserves customer-owned work evidence, structures it into searchable context and process telemetry, and exposes it through REST/MCP so ChatGPT, Claude and other AI systems can understand how work is actually performed — not only what is written in documents and business systems.

[![Tests](https://github.com/KAVentures/openworkgraph/actions/workflows/tests.yml/badge.svg)](https://github.com/KAVentures/openworkgraph/actions/workflows/tests.yml)
[![Latest release](https://img.shields.io/github/v/release/KAVentures/openworkgraph)](https://github.com/KAVentures/openworkgraph/releases/latest)

> **Project status:** early public prototype. The current goal is accurate local capture, reconstructable work context and AI access — not employee-performance scoring or a production enterprise control plane.

## Try it

### macOS

**[⬇ Download the latest macOS tester ZIP](https://github.com/KAVentures/openworkgraph/releases/latest/download/OpenWorkGraph-macOS.zip)**

1. Download and unzip `OpenWorkGraph-macOS.zip`.
2. Right-click **`START_OPENWORKGRAPH.command` → Open**.
3. Confirm **Open** if macOS asks.
4. On first launch, OpenWorkGraph downloads its own private runtime. No system Python is required.
5. Approve **Accessibility** and **Input Monitoring** if macOS requests them.
6. The local dashboard opens automatically at `http://127.0.0.1:8787`.

Then double-click **`ADD_BROWSER_SENSOR.command`** once and follow the on-screen instructions to load the browser sensor.

### Windows

**[⬇ Download the latest Windows tester ZIP](https://github.com/KAVentures/openworkgraph/releases/latest/download/OpenWorkGraph-Windows.zip)**

1. Download and unzip `OpenWorkGraph-Windows.zip`.
2. Double-click **`START_OPENWORKGRAPH.cmd`**.
3. If Windows shows a security warning for this early unsigned prototype, review the source and proceed only if you trust this repository.
4. On first launch, OpenWorkGraph downloads its own private runtime. **No Python installation is required.**
5. The local dashboard opens automatically at `http://127.0.0.1:8787`.

Then double-click **`ADD_BROWSER_SENSOR.cmd`** once and follow the on-screen instructions to load the browser sensor in Chrome or Edge.

Windows now includes best-effort **Microsoft UI Automation** metadata for native controls such as buttons, menus and fields. OpenWorkGraph reads control identity/label metadata only; it deliberately does not request typed values, selected text or password values. Some elevated applications or applications without a UI Automation provider may expose less semantic detail.

### One command for Codex / Claude Code / terminal users

If an agent has shell access, you can give it one command instead of downloading the ZIP manually.

**macOS:**

```bash
curl -fsSL https://raw.githubusercontent.com/KAVentures/openworkgraph/main/install.sh | bash
```

**Windows PowerShell:**

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/KAVentures/openworkgraph/main/install.ps1 | iex"
```

These commands download the **latest GitHub Release** and invoke the same standalone launcher used by ordinary testers. If you prefer not to execute a remote script directly, inspect `install.sh` / `install.ps1` in this repository first and use the Release ZIP instead.

The browser extension still requires a one-time browser approval because ordinary local software should not silently install browser extensions.

### Source ZIP also works

`Code → Download ZIP` remains usable. On macOS run `START_ON_MAC.command`; on Windows run `START_ON_WINDOWS.bat`. The Release ZIPs are cleaner for nontechnical testers.

---

## What OpenWorkGraph is

Most enterprise AI can search what an organization has already written down: documents, email, Slack, CRM records, tickets and knowledge bases.

OpenWorkGraph is aimed at a different missing layer:

> **What did people actually do, in what order, across which tools, with how much effort, and what normally happens in practice?**

A captured trace might look conceptually like:

```text
09:02  Gmail          read customer request
09:04  Salesforce     search account
09:05  Google Sheets  check pricing
09:07  Teams          ask colleague
09:10  Salesforce     update case
09:11  Gmail          reply
```

That work history can then be queried by an AI through MCP/API for questions such as:

- “What do we normally do in this situation?”
- “Show me similar work from the past.”
- “Where are we spending the most manual effort?”
- “Which repeated processes look suitable for automation?”
- “What internal tool would eliminate the most recurring work?”
- “Did the new tool actually reduce the time spent on this process?”

## Architecture

```text
 Desktop sensor ───────┐
 Browser sensor ───────┤
 Future sensors ───────┤
                       ▼
                RAW WORK EVIDENCE
              customer-owned truth
                       │
             ┌─────────┴─────────┐
             ▼                   ▼
       CONTEXT LAYER       OPERATIONAL LAYER
     resources/history      tasks/effort/
      searchable memory    patterns/processes
             │                   │
             └─────────┬─────────┘
                       ▼
                    REST / MCP
                       │
          ┌────────────┼────────────┐
          ▼            ▼            ▼
       ChatGPT       Claude    internal agents
```

The observer does not need to permanently decide what a workflow “means.” It preserves reconstructable evidence so better models can reinterpret the same history later.

## Three local data layers

OpenWorkGraph deliberately separates capture fidelity from downstream privacy/analysis policy.

1. **Raw local evidence** — the richest customer-owned source of truth for reconstruction and verification.
2. **Customer context** — searchable organizational memory containing useful observed resource/page/window/UI context, while never adding typed field values or clipboard contents.
3. **Normalized operational events** — a content-minimized representation used for broad process/effort analytics and task inference.

## What the observer captures

Current capture includes:

- active application and window/tab focus spans
- browser tab activation and navigation
- brief address-bar navigation without requiring a later click or scroll
- foreground, engaged, probable-idle and active-input timing
- aggregate keypress counts — **never key identities, key order or typed text**
- global mouse clicks and throttled scrolls
- native semantic control metadata on macOS Accessibility and Windows UI Automation, best effort
- browser semantic events such as interactive clicks, editor/input focus, form submits and control changes
- copy/paste **occurrence**, not clipboard contents
- candidate task executions
- repeated completed task families
- navigation fragments kept separate from completed-task evidence

## Reliable event delivery

Desktop capture is written locally first and placed into a durable SQLite outbox. If the local API is temporarily unavailable, the same event remains queued and is retried with its original event ID.

The browser sensor has a separate durable queue in extension storage. Browser observations keep stable sensor identity and the work-session identity from the moment they were captured, so delayed delivery cannot silently attach old evidence to a later session.

## Identity model

Events use a versioned identity envelope with:

- `schema_version`
- optional `organization_id`
- optional `actor_id`
- stable `device_id`
- stable `sensor_id`
- `session_id`
- `source`

A local installation identity persists across restarts. Organization and actor IDs can be configured later for multi-user deployments.

## Task and process inference

Task inference runs on normalized operational events rather than arbitrary raw titles. This makes repeated-task matching less dependent on email subjects, recipients, document names or record identifiers.

For example, different email executions can normalize to the same task family:

```text
Gmail → Compose → typing effort → Send → email.compose_send
```

Repeated task families are evidence for review — not a claim that a task is automatically safe to automate.

## MCP: organizational memory + workflow analysis

The MCP server can expose OpenWorkGraph directly to an AI assistant.

Context-oriented tools include:

- `get_current_work_context`
- `search_work_history`
- `find_similar_work`
- `get_context_session`
- `find_process_examples`

Operational tools include workflow summaries, normalized observation search, candidate task executions, session traces and automation candidates.

Raw local evidence is not exposed through MCP by default.

## REST data layers

### Raw local evidence

- `GET /v1/summary?scope=current`
- `GET /v1/events`
- `GET /v1/semantic-activity`
- `GET /v1/sessions/{session_id}`

### Customer-owned context

- `GET /v1/context-events?query=...`
- `GET /v1/context-sessions/{session_id}`

### Content-minimized operational telemetry

- `GET /v1/operational-summary?scope=current`
- `GET /v1/operational-events`
- `GET /v1/operational-semantic-activity`
- `GET /v1/operational-sessions/{session_id}`
- `GET /v1/tasks?scope=current`

### Sensor coordination / ingestion

- `GET /v1/browser-context`
- `POST /v1/events`
- `POST /v1/browser-events`
- `POST /v1/heartbeat`
- `POST /v1/browser-heartbeat`

## Browser navigation accuracy

The browser sensor uses several independent browser-native signals so creating a tab, typing a URL and pressing Enter is captured without waiting for a later page interaction:

- `tabs.onUpdated`
- `webNavigation.onBeforeNavigate`
- `webNavigation.onCommitted`
- `webNavigation.onCompleted`
- document-start observation
- History API route changes for single-page applications

Failed delivery is queued locally and retried.

## Platform status

| Capability | macOS | Windows |
| --- | --- | --- |
| Standalone no-Python tester ZIP | ✅ | ✅ |
| Active app/window telemetry | ✅ | ✅ |
| Key/click/scroll effort | ✅ | ✅ |
| Browser navigation + semantic events | ✅ | ✅ |
| Durable local delivery | ✅ | ✅ |
| Raw/context/operational layers | ✅ | ✅ |
| Exports / REST / MCP | ✅ | ✅ |
| Native control semantics | Accessibility API | Microsoft UI Automation |
| Automated CI | ✅ | ✅ |

Native control semantics are best effort on both platforms. Windows cannot inspect controls from every application, especially when the target application runs at a higher integrity level than OpenWorkGraph or does not expose a UI Automation provider.

## Exports

The dashboard can export the captured session as:

- JSON
- XLSX
- ZIP of CSV tables

The normalized operational representation is available for lower-content analysis. A deliberate **Include rich raw session evidence** option includes the richer customer-owned evidence when full reconstruction is needed.

## Data and privacy boundary

The current prototype server binds to `127.0.0.1`. Captured data stays on the local computer unless the user deliberately exports it or later connects it to another system.

OpenWorkGraph's collection model intentionally avoids storing key identities/typed text in the effort counter and does not capture clipboard contents. Rich raw evidence can still contain sensitive visible resource names, page titles or other on-screen context, so enterprise deployment will require organization-specific collection policies, authentication, encryption, RBAC, retention rules and audit logs.

## Development

Python 3.11+ is supported for source development. Standalone tester packages bring their own private Python runtime.

The test suite runs in GitHub Actions on macOS, Windows and Linux.

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
```

## Releases

`VERSION` is the canonical project version. When a new version is pushed to `main`, GitHub Actions builds both `OpenWorkGraph-macOS.zip` and `OpenWorkGraph-Windows.zip`. If that version does not already have a GitHub Release, the workflow creates one and attaches both standalone tester ZIPs plus SHA-256 checksums.

Existing releases are left immutable; bump `VERSION` to publish a new release.
