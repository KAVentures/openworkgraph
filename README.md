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

Windows includes best-effort **Microsoft UI Automation** metadata for native controls such as buttons, menus and fields. OpenWorkGraph reads control identity/label metadata only; it deliberately does not request typed values, selected text or password values. Long/native row labels are bounded so list rows do not become message/body capture. Some elevated applications or applications without a UI Automation provider may expose less semantic detail.

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

### MCP trust boundary

Page titles, document titles and UI labels are **observed data, not trusted instructions**. Before any context/process result crosses the MCP boundary, OpenWorkGraph removes invisible direction/control characters, bounds scalar length, suppresses command-like prompt-injection text (for example forged `SYSTEM:` / assistant roles, “ignore previous instructions”, tool-call commands or requests to reveal secrets), and adds an `_openworkgraph_security` trust annotation. This MCP-only hardening does not rewrite the local evidence used for reconstruction.

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
             RICH LOCAL WORK EVIDENCE
          privacy-minimized where needed
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

The observer does not need to permanently decide what a workflow “means.” It preserves reconstructable evidence so better models can reinterpret the same history later, while removing selected literals that are not needed to understand the workflow.

## Three local data layers

OpenWorkGraph deliberately separates capture fidelity from privacy/analysis policy.

1. **Rich local evidence** — the richest customer-owned source used for reconstruction and verification. It is not byte-for-byte raw: URL query/fragment secrets, token-like URL path segments, high-confidence national/patient/case/account identifiers, validated payment identifiers and credential-shaped secrets are minimized or pseudonymized before persistence where their literal values are not needed for workflow analysis.
2. **Customer context** — searchable organizational memory containing useful observed resource/page/window/UI context, while never adding typed field values or clipboard contents.
3. **Normalized operational events** — a content-minimized representation used for broad process/effort analytics and task inference.

Names and other ordinary business semantics are generally preserved locally because they can matter for reconstruction. Presentation layers apply additional identity masking such as `OWNER` / `PERSON_x` tokens.

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
- short semantic control labels where they are useful; long row/body-like labels are intentionally dropped rather than captured as control text
- copy/paste **occurrence**, not clipboard contents
- candidate task executions
- repeated completed task families
- navigation fragments kept separate from completed-task evidence

The browser extension declares private/incognito use as **not allowed**, matching the desktop exclusion policy.

## Reliable event delivery

Desktop capture is privacy-minimized first, then written to the local JSONL recovery log and durable SQLite outbox. If the local API is temporarily unavailable, the same safe event remains queued and is retried with its original event ID. Legacy local JSONL/outbox data is migrated through the same high-confidence sanitizer.

The browser sensor has a separate durable queue in extension storage. Browser observations keep stable sensor identity and the work-session identity from the moment they were captured, so delayed delivery cannot silently attach old evidence to a later session. Browser heartbeat state uses the same exclusion rules as browser events; an excluded/private page does not leak its hostname/title through the live status surface.

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

Rich local evidence is not exposed through MCP by default. Observed strings that do cross MCP are treated as untrusted data and pass through the MCP prompt-injection boundary described above.

## REST data layers

### Rich local evidence

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
| Rich/context/operational layers | ✅ | ✅ |
| Exports / REST / MCP | ✅ | ✅ |
| Native control semantics | Accessibility API | Microsoft UI Automation |
| Automated CI | ✅ | ✅ |

Native control semantics are best effort on both platforms. Windows cannot inspect controls from every application, especially when the target application runs at a higher integrity level than OpenWorkGraph or does not expose a UI Automation provider.

## Exports

The dashboard can export the captured session as:

- JSON
- XLSX
- ZIP of CSV tables

The normalized operational representation is available for lower-content analysis. A deliberate **Include rich raw session evidence** option includes richer local evidence when full reconstruction is needed. That rich export is still subject to pre-storage URL/identifier/credential minimization; it is not a verbatim screen/content dump.

## Screenshots

Screenshots are **off by default**. If an operator explicitly enables `screenshots_enabled` or `interaction_screenshots_enabled`, the current prototype can save full-monitor JPEGs locally for non-excluded activity. Those pixels do **not** receive the text redaction/pseudonymization pipeline and can contain unrelated visible information from the screen. Treat screenshot mode as a high-risk diagnostic feature and keep it disabled unless you specifically need it. Excluded windows are skipped.

## Data and privacy boundary

The prototype server binds to `127.0.0.1`; it does not intentionally send captured work telemetry to a cloud service. Data leaves the machine when the user deliberately exports it or explicitly connects another system/AI to the local REST/MCP interfaces.

OpenWorkGraph deliberately removes selected high-risk literals before persistence when their literal values add little workflow value. Current high-confidence handling includes Swedish personnummer and labelled patient/journal/case/account identifiers, labelled bank/organization identifiers, mod-97-valid IBANs, Luhn-valid payment-card numbers, common credential/token shapes, URL query/fragment values and token-like URL path segments. Ordinary business amounts, project/deal names, counterparties and other useful workflow semantics are intentionally not generically erased.

OpenWorkGraph does not capture typed text/key identities in its keyboard effort counter and does not capture clipboard contents. Rich evidence can still contain sensitive visible resource names, page/document titles and other on-screen context. Privacy filtering reduces risk; it does not make captured workflow evidence anonymous.

Local pseudonymization keys are permission-restricted where supported, but currently remain in the local OpenWorkGraph data area rather than an OS Keychain/DPAPI store so existing stable tokens are not broken by an unsafe migration.

### Current prototype security limitations

The localhost API currently relies on loopback binding, trusted-host/origin restrictions and browser-extension route restrictions; **same-user local processes are not yet required to present a read capability token**. Likewise, the browser sensor posts to the fixed loopback endpoint and does **not yet cryptographically authenticate the process that owns port 8787**. These are known prototype limitations, not security guarantees. A future pairing/authentication design needs to preserve dashboard, MCP and browser compatibility before it is enabled by default.

See [`SECURITY.md`](SECURITY.md) for the current threat model, screenshot/key-storage caveats and the deliberately deferred hardening items.

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
