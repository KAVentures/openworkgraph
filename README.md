# OpenWorkGraph

**A local-first context layer for how work actually happens.**

OpenWorkGraph observes desktop and browser work, preserves useful customer-owned workflow evidence, structures it into searchable context and process telemetry, and makes it available for manual export or controlled AI access through MCP.

[![Tests](https://github.com/KAVentures/openworkgraph/actions/workflows/tests.yml/badge.svg)](https://github.com/KAVentures/openworkgraph/actions/workflows/tests.yml)
[![Latest release](https://img.shields.io/github/v/release/KAVentures/openworkgraph)](https://github.com/KAVentures/openworkgraph/releases/latest)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

> **Project status:** early public prototype. The current goal is accurate local capture, reconstructable work context and useful AI access — not employee-performance scoring or a finished enterprise control plane.

## Try it

### macOS

**[⬇ Download the latest macOS tester ZIP](https://github.com/KAVentures/openworkgraph/releases/latest/download/OpenWorkGraph-macOS.zip)**

1. Download and unzip `OpenWorkGraph-macOS.zip`.
2. Right-click **`START_OPENWORKGRAPH.command` → Open**.
3. Confirm **Open** if macOS asks.
4. On first launch, OpenWorkGraph downloads its own private runtime. No system Python is required.
5. Approve **Accessibility** and **Input Monitoring** if macOS requests them.
6. The local dashboard opens automatically at `http://127.0.0.1:8787`.

Then double-click **`ADD_BROWSER_SENSOR.command`** once and follow the on-screen instructions to load the optional browser sensor.

### Windows

**[⬇ Download the latest Windows tester ZIP](https://github.com/KAVentures/openworkgraph/releases/latest/download/OpenWorkGraph-Windows.zip)**

1. Download and unzip `OpenWorkGraph-Windows.zip`.
2. Double-click **`START_OPENWORKGRAPH.cmd`**.
3. If Windows shows a security warning for this early unsigned prototype, review the source and proceed only if you trust this repository.
4. On first launch, OpenWorkGraph downloads its own private runtime. No system Python is required.
5. The local dashboard opens automatically at `http://127.0.0.1:8787`.

Then double-click **`ADD_BROWSER_SENSOR.cmd`** once and load the opened `browser_extension` folder in Chrome or Edge.

Windows includes best-effort **Microsoft UI Automation** metadata for native controls. OpenWorkGraph reads safe control identity/label metadata, not typed field values, selected text or password values.

### One command for agent / terminal users

**macOS**

```bash
curl -fsSL https://raw.githubusercontent.com/KAVentures/openworkgraph/main/install.sh | bash
```

**Windows PowerShell**

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/KAVentures/openworkgraph/main/install.ps1 | iex"
```

If you do not want to execute a remote script directly, inspect the script first or use the Release ZIP.

---

## What OpenWorkGraph is

Most enterprise AI can search what an organization has already written down: documents, email, chat, CRM records, tickets and knowledge bases.

OpenWorkGraph targets a different missing layer:

> **What did people actually do, in what order, across which tools, with how much effort, and what normally happens in practice?**

A captured trace might conceptually look like:

```text
09:02  Gmail          read customer request
09:04  Salesforce     search account
09:05  Google Sheets  check pricing
09:07  Teams          ask colleague
09:10  Salesforce     update case
09:11  Gmail          reply
```

An AI can then ask questions such as:

- “What do we normally do in this situation?”
- “Show me similar work from the past.”
- “Where are we spending the most manual effort?”
- “Which repeated processes look suitable for automation?”
- “What internal tool would eliminate the most recurring work?”
- “Did the new tool actually reduce the time spent on this process?”

## What is captured

Current capture includes:

- active application and window/tab focus spans
- browser tab activation and navigation
- brief address-bar navigation without requiring a later click or scroll
- foreground, engaged, probable-idle and active-input timing
- aggregate keypress counts — **never key identities, key order or typed text**
- global mouse clicks and throttled scrolls
- native semantic control metadata through macOS Accessibility and Windows UI Automation, best effort
- browser semantic events such as interactive clicks, editor/input focus, form submits and control changes
- copy/paste **occurrence**, not clipboard contents
- candidate task executions and repeated completed task families derived from the event stream
- navigation fragments kept separate from completed-task evidence

### Deliberately not captured in normal operation

- typed text or individual key identities
- clipboard contents
- password-field values
- selected text
- screenshots or screen recording by default
- URL query strings or fragments in browser evidence

Screenshots are disabled in the normal configuration and are not part of the current product direction or normal exports.

## Why the browser sensor matters

Desktop observation can tell that a browser is in use, but browser-native signals make the evidence much more useful by separating Gmail, Google Docs, Salesforce, ChatGPT and other web tools and by capturing navigation reliably.

The extension is optional. Desktop capture continues without it, but browser-level context is less precise.

## Architecture

```text
 Desktop sensor ───────┐
 Browser sensor ───────┤
 Future sensors ───────┤
                       ▼
                RAW WORK EVIDENCE
               local source events
                       │
             ┌─────────┴─────────┐
             ▼                   ▼
       CONTEXT LAYER       OPERATIONAL LAYER
     resources/history      tasks/effort/
      searchable memory    patterns/processes
             │                   │
             └─────────┬─────────┘
                       ▼
              REST / export / MCP
```

The observer does not need to permanently decide what a workflow “means.” It preserves reconstructable event evidence so later analytics or better models can reinterpret the history.

## Three local data layers

1. **Raw local evidence** — the richest persisted event layer used for reconstruction and verification. “Raw” does not mean unsanitized: high-confidence sensitive identifiers and secrets are hardened before persistence.
2. **Customer context** — searchable organizational memory derived from observed resource/page/window/UI context.
3. **Normalized operational events** — a content-minimized representation used for broad process/effort analytics and task inference.

## Privacy model

OpenWorkGraph deliberately separates capture fidelity from downstream privacy/presentation policy.

### Storage-time sensitive-identifier hardening

Before event evidence is persisted, high-confidence identifiers are pseudonymized or removed where their literal value is unnecessary for workflow analysis. Current handling includes, among other shapes:

- Swedish personal identifiers and explicitly labelled patient/journal/case/account IDs
- IBANs and recognized payment-card numbers
- environment-variable/API credentials, bearer tokens, connection-string passwords and private-key blocks
- other long Luhn-valid sensitive numbers when the system cannot confidently classify them

Swedish OCR/payment references, invoice/reference numbers and similar business references are not intentionally labelled as payment cards merely because they pass Luhn. Explicit reference cues take precedence over card-shape heuristics.

### Presentation/export person and owner pseudonymization

When evidence is presented through dashboard/API/MCP/export surfaces, additional presentation policy can replace detected people with stable `PERSON_x` tokens and the local user with `OWNER`.

The dashboard includes **Reset learned person aliases**. This deletes the local alias registry only; it does not delete or rewrite captured workflow history.

See [Privacy and data handling](docs/PRIVACY_AND_DATA.md).

## Local interface security

OpenWorkGraph does **not** treat `localhost` as authentication.

The normal launcher creates installation-local capability credentials and protects the local interfaces:

- raw/history/export/control API routes require an authenticated dashboard session or local API capability
- desktop collector writes are authenticated so another local process cannot silently inject fabricated workflow history
- the dashboard exchanges a launcher-only URL-fragment bootstrap for an HttpOnly local session; the long-lived API capability is not embedded in dashboard HTML
- the browser sensor verifies the genuine OpenWorkGraph server with HMAC challenge-response **before browser evidence is sent**
- browser requests are HMAC-signed with timestamp/nonces and replay protection
- local MCP clients use **stdio** by default, avoiding a standing MCP network port
- when HTTP MCP is explicitly requested, it is bearer-protected, started on an available loopback port, and proves a random launch-time instance nonce before the dashboard advertises it

If browser authentication fails, the existing extension queue keeps pending evidence rather than sending it to an unverified listener. If port 8787 is already occupied when OpenWorkGraph starts, capture does not start; it will not reuse an unknown localhost service.

These controls materially reduce accidental localhost exposure, unrelated-service probing, naive port squatting and unauthenticated local access. **They are not a security boundary against malware already running with the same operating-system user privileges.** Stronger protection for that threat model requires OS isolation, endpoint security and/or managed enterprise controls.

## What remains intentionally useful

OpenWorkGraph does not try to erase every business fact. Depending on observable titles/labels, rich local evidence can intentionally retain:

- company/customer names
- project or deal names
- order/reference/invoice numbers
- amounts
- document/page/window titles
- safe UI labels

Those details can be essential for understanding a workflow. They can also be sensitive.

---

# Using OpenWorkGraph with AI

There are three intentionally different paths.

## 1. Export and upload — universal/default

The dashboard exports a captured session as:

- **XLSX** — convenient for ChatGPT/Claude and manual inspection
- **CSV ZIP** — convenient for ChatGPT/Claude/data tools and includes `README_FOR_AI.md`
- **JSON** — full structured representation intended primarily for code/integrations

The dashboard includes **Include rich raw session evidence**. Leave it enabled when detailed reconstruction matters; disable it for a more content-minimized operational export.

This is the simplest path for any AI that accepts files. The user can inspect exactly what is being shared before uploading it.

## 2. Local MCP — Claude Desktop, Cursor and other local MCP clients

Local clients use **stdio by default**:

```text
AI application
      │
      │ stdio
      ▼
OpenWorkGraph MCP
      │
      ▼
authenticated local API → local evidence
```

No separate MCP network port is kept open during normal OpenWorkGraph operation.

### AI access switch

MCP reading is **OFF on every OpenWorkGraph launch**.

The dashboard contains an **AI access** control. Connecting a local AI can enable access for the current run; turning it off causes subsequent MCP tool calls — including already-configured clients — to be denied immediately.

The setting is intentionally process-local and resets to OFF when OpenWorkGraph restarts.

### Local MCP activity

The dashboard keeps a short local activity view showing, for example:

```text
20:12  get_workflow_trace       96 rows · 41 KB · 14:02–15:18
20:14  automation_candidates     7 rows · 3 KB
```

The default audit record contains the tool name, time, result-row count, approximate returned bytes and evidence time range. Search terms/tool arguments are deliberately not stored in the activity log by default.

### Cursor

The dashboard’s **Add to Cursor** action installs an stdio MCP configuration using Cursor’s native confirmation flow. The configuration points at OpenWorkGraph’s existing private Python runtime and `mcp_server.secure_stdio`; it does not place the long-lived HTTP MCP bearer token in the Cursor deep link.

### Claude Desktop

The v0.49+ GitHub Release includes:

**`OpenWorkGraph-Claude.mcpb`**

This is a small Claude Desktop extension wrapper. It does not contain workflow history. It launches the authenticated OpenWorkGraph stdio MCP server from an existing OpenWorkGraph installation.

**[⬇ Download the latest Claude Desktop extension](https://github.com/KAVentures/openworkgraph/releases/latest/download/OpenWorkGraph-Claude.mcpb)**

Install OpenWorkGraph first, then install/approve the `.mcpb` in Claude Desktop. The dashboard also provides a manual stdio configuration as a fallback for clients or environments where desktop-extension installation is unavailable.

## 3. ChatGPT live / network-only MCP — advanced

ChatGPT cannot directly start the local OpenWorkGraph stdio process. For ChatGPT live access, the dashboard can explicitly start an authenticated **HTTP MCP endpoint on demand** and show the real endpoint/authorization value for use with OpenAI’s supported Secure MCP Tunnel/custom-app flow.

HTTP MCP is not started during ordinary OpenWorkGraph launch. It stops with OpenWorkGraph or when the user explicitly stops it from the dashboard.

The endpoint is not assumed to be `8788`: OpenWorkGraph uses an available loopback port and verifies a random launch-time identity nonce from the child process before advertising the endpoint.

Do not expose the local MCP port directly to the public internet.

## Compact rich MCP evidence

MCP remains **rich-evidence-first**, but v0.49 no longer returns the same events repeatedly as raw/context/semantic copies.

The canonical evidence tool is:

```text
get_workflow_trace(since, until, cursor, limit, scope)
```

It returns a compact chronological table containing the useful workflow semantics — timestamps, app, title, event/action, target label/role, page host/path and effort counts — while omitting internal identifiers and storage-only fields that do not help the model answer the user’s question.

Default tool responses are bounded and paginated. A response includes `has_more` and `next_cursor`; the model asks for another page deliberately instead of receiving the entire database in one call.

Pagination uses a stable `(observed_at, database id)` cursor and a frozen snapshot boundary. Events arriving while an AI is paging through older work therefore do not create duplicate/skipped rows or move the result set underneath it.

Summary/task tools return compact derived views and point back to `get_workflow_trace` when supporting evidence is needed.

Current MCP tools:

- `get_workflow_trace`
- `get_current_work_context`
- `search_work_history`
- `find_similar_work`
- `get_context_session`
- `find_process_examples`
- `company_workflow_summary`
- `search_work_observations`
- `recent_semantic_activity`
- `candidate_task_executions`
- `get_work_session`
- `automation_candidates`

MCP also exposes:

- `openworkgraph://ai-guide` — the same AI data dictionary bundled as `README_FOR_AI.md` in exports
- `openworkgraph://data-model` — concise guidance on the OpenWorkGraph evidence model

### MCP trust boundary

Page titles, document titles and UI labels are **observed data, not trusted instructions**. Before results cross the MCP boundary, OpenWorkGraph removes invisible direction/control characters, bounds scalar length, suppresses common command-like prompt-injection text and attaches an `_openworkgraph_security` trust annotation.

This is separate from storage-time identifier hardening and local interface authentication.

---

## Reliable event delivery

Desktop capture is written locally first and placed into a durable SQLite outbox. If the local API is temporarily unavailable, the same event remains queued and is retried with its original event ID.

The browser sensor has a separate durable queue in extension storage. Browser evidence remains queued when the paired local server cannot be authenticated.

## Task and process inference

Task inference runs on normalized operational events rather than arbitrary raw titles. Repeated task families are evidence for review — not a claim that a task is automatically safe to automate.

For example, different email executions can normalize toward a stable family such as:

```text
Gmail → Compose → typing effort → Send → email.compose_send
```

## REST data layers

The `/v1/*` data/control routes are authenticated in normal operation. Browser/collector ingestion uses its own paired/capability-authenticated paths.

### Raw / context / operational evidence

- `GET /v1/summary?scope=current`
- `GET /v1/events`
- `GET /v1/semantic-activity`
- `GET /v1/sessions/{session_id}`
- `GET /v1/context-events?query=...`
- `GET /v1/context-sessions/{session_id}`
- `GET /v1/operational-summary?scope=current`
- `GET /v1/operational-events`
- `GET /v1/operational-semantic-activity`
- `GET /v1/operational-sessions/{session_id}`
- `GET /v1/tasks?scope=current`

### AI/MCP control and compact trace

- `GET /v1/workflow-trace`
- `GET/POST /v1/ai-access`
- `GET/POST /v1/mcp-activity`
- `GET/POST /v1/mcp-http`
- `GET /v1/mcp-connection-config`

### Exports / privacy controls

- `GET /v1/export/json`
- `GET /v1/export/xlsx`
- `GET /v1/export/csvzip`
- `POST /v1/privacy/reset-learned-names`

### Sensor coordination / ingestion

- `GET /v1/browser-context`
- `POST /v1/events`
- `POST /v1/browser-events`
- `POST /v1/heartbeat`
- `POST /v1/browser-heartbeat`

## Platform status

| Capability | macOS | Windows |
| --- | --- | --- |
| Standalone no-Python tester ZIP | ✅ | ✅ |
| Active app/window telemetry | ✅ | ✅ |
| Key/click/scroll effort | ✅ | ✅ |
| Browser navigation + semantic events | ✅ | ✅ |
| Durable local delivery | ✅ | ✅ |
| Raw/context/operational layers | ✅ | ✅ |
| XLSX / CSV ZIP / JSON exports | ✅ | ✅ |
| Authenticated REST | ✅ | ✅ |
| Local stdio MCP | ✅ | ✅ |
| Optional authenticated HTTP MCP | ✅ | ✅ |
| Paired browser sensor | ✅ | ✅ |
| Native control semantics | Accessibility API | Microsoft UI Automation |
| Automated CI | ✅ | ✅ |

Native control semantics are best effort on both platforms.

## Current limitations

OpenWorkGraph is not yet a finished enterprise product. Current limitations include:

- browser semantic detail requires installation/approval of the optional extension
- some native applications expose weak or no accessibility/UI Automation labels
- activity timing is an estimate; a foreground window is not proof a person was actively working every second
- inferred tasks/patterns are analytical interpretations, not ground truth
- rich local evidence can still contain sensitive business context after high-confidence identifier hardening
- every MCP tool result sent to an AI is data sent to that AI provider for processing; use the AI-access switch deliberately
- AI clients differ in MCP support and managed workspaces may require administrator approval
- local capability/pairing controls are not intended to protect against malware already running with the same OS-user privileges
- enterprise-wide RBAC, centrally managed policy, retention/audit controls, encryption policy and fleet deployment are not yet a production control plane

## Documentation

- [Documentation index](docs/README.md)
- [Privacy and data handling](docs/PRIVACY_AND_DATA.md)
- [Exports and AI analysis](docs/EXPORTS_AND_AI.md)
- [Owner/person presentation redaction](docs/OWNER_REDACTION.md)
- [Nontechnical testing guide](NONTECHNICAL_TESTING.md)
- [Standalone macOS launcher notes](docs/STANDALONE_MAC_LAUNCHER.md)

## Development

Python 3.11+ is supported for source development. Standalone tester packages bring their own private Python runtime.

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
```

The test suite runs in GitHub Actions on macOS, Windows and Linux. Browser JavaScript, runtime-injected dashboard JavaScript, a real stdio MCP handshake/tool call, HTTP MCP authentication, stable pagination and package builds are covered by automated regression tests.

## Releases

`VERSION` is the canonical project version. When a new version is pushed to `main`, GitHub Actions builds:

- `OpenWorkGraph-macOS.zip`
- `OpenWorkGraph-Windows.zip`
- `OpenWorkGraph-Claude.mcpb`

Existing releases are left immutable; bump `VERSION` to publish a new GitHub Release.

## License

OpenWorkGraph is open-source software licensed under the [Apache License 2.0](LICENSE).

Copyright © 2026 Koyar Afrasyab (Kinvectum).

Apache-2.0 permits commercial use, modification and redistribution subject to its terms and includes an express patent grant from contributors. The software license does not grant rights to OpenWorkGraph or Kinvectum names, logos or other branding except as required for reasonable attribution and describing the origin of the software.
