# OpenWorkGraph

**A local-first context layer for how work actually happens.**

OpenWorkGraph observes desktop and browser work, preserves useful customer-owned workflow evidence, structures it into searchable context and process telemetry, and exposes it through REST/MCP so ChatGPT, Claude and other AI systems can reason about how work is actually performed — not only what is written in documents and business systems.

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

Then double-click **`ADD_BROWSER_SENSOR.command`** once and follow the on-screen instructions to load the optional browser sensor.

### Windows

**[⬇ Download the latest Windows tester ZIP](https://github.com/KAVentures/openworkgraph/releases/latest/download/OpenWorkGraph-Windows.zip)**

1. Download and unzip `OpenWorkGraph-Windows.zip`.
2. Double-click **`START_OPENWORKGRAPH.cmd`**.
3. If Windows shows a security warning for this early unsigned prototype, review the source and proceed only if you trust this repository.
4. On first launch, OpenWorkGraph downloads its own private runtime. **No Python installation is required.**
5. The local dashboard opens automatically at `http://127.0.0.1:8787`.

Then double-click **`ADD_BROWSER_SENSOR.cmd`** once and follow the on-screen instructions to load the optional browser sensor in Chrome or Edge.

Windows includes best-effort **Microsoft UI Automation** metadata for native controls such as buttons, menus and fields. OpenWorkGraph reads control identity/label metadata only; it deliberately does not request typed field values, selected text or password values. Some elevated applications or applications without a UI Automation provider expose less semantic detail.

### One command for Codex / Claude Code / terminal users

If an agent has shell access, it can install the latest Release directly.

**macOS**

```bash
curl -fsSL https://raw.githubusercontent.com/KAVentures/openworkgraph/main/install.sh | bash
```

**Windows PowerShell**

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/KAVentures/openworkgraph/main/install.ps1 | iex"
```

These commands download the latest GitHub Release and invoke the same standalone launcher used by ordinary testers. If you do not want to execute a remote script directly, inspect `install.sh` / `install.ps1` first or use the Release ZIP.

The browser extension still requires one-time browser approval; local software should not silently install browser extensions.

### Source ZIP

`Code → Download ZIP` also works. On macOS run `START_ON_MAC.command`; on Windows run `START_ON_WINDOWS.bat`. The Release ZIPs are the recommended path for nontechnical testers.

---

## What OpenWorkGraph is

Most enterprise AI can search what an organization has already written down: documents, email, Slack, CRM records, tickets and knowledge bases.

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

Desktop observation can tell that a browser is in use, but browser-native signals make the data much more useful by separating Gmail, Google Docs, Salesforce, ChatGPT and other web tools and by capturing navigation reliably.

The browser sensor uses multiple independent signals, including tab activation/update and browser navigation lifecycle events. Creating a tab, typing a URL and pressing Enter can therefore produce evidence without requiring a later click or scroll.

The extension is optional: desktop capture continues without it, but browser-level context is less precise.

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
                    REST / MCP
                       │
          ┌────────────┼────────────┐
          ▼            ▼            ▼
       ChatGPT       Claude    internal agents
```

The observer does not need to permanently decide what a workflow “means.” It preserves reconstructable event evidence so later analytics or better models can reinterpret the history.

## Three local data layers

OpenWorkGraph separates capture fidelity from downstream privacy and analysis policy.

1. **Raw local evidence** — the richest persisted event layer used for reconstruction and verification. “Raw” does **not** mean unsanitized: high-confidence sensitive identifiers and secrets are hardened before persistence.
2. **Customer context** — searchable organizational memory derived from observed resource/page/window/UI context.
3. **Normalized operational events** — a content-minimized representation used for broad process/effort analytics and task inference.

## Privacy model

OpenWorkGraph has two distinct privacy stages. Keeping them separate is important.

### 1. Storage-time sensitive-identifier hardening

Before event evidence is persisted, high-confidence identifiers are pseudonymized or removed where the literal value is unnecessary for workflow analysis. Current handling includes, among other shapes:

- Swedish personal identifiers and explicitly labelled patient/journal/case/account IDs
- IBANs and recognized payment-card numbers
- environment-variable/API credentials, bearer tokens, connection-string passwords and private-key blocks
- other long Luhn-valid sensitive numbers when the system cannot confidently classify them as cards

Swedish OCR/payment references, invoice/reference numbers and similar business references are **not intentionally labelled as payment cards merely because they pass Luhn**. Explicit reference cues take precedence over card-shape heuristics.

Ambiguous long Luhn-valid numbers may be represented as `SENSITIVE_NUMBER_x` rather than being exposed or falsely described as a card.

### 2. Presentation/export person and owner pseudonymization

When data is presented through dashboard/API/MCP/export surfaces, additional presentation policy can replace personal aliases with stable tokens such as `PERSON_x` and the local user with `OWNER`.

Persistent person learning is deliberately conservative. Strong evidence such as `Name <email>`, sender/recipient fields, `reply to`, `from`, `cc`, `bcc`, `meeting with` and similar person-specific contexts can teach an alias. Generic workflow phrases and status/team language such as `In Progress`, `Legal Team`, queues, boards and sprint labels are rejected from persistent person learning.

The dashboard includes **Reset learned person aliases**. This deletes the local alias registry only; it does not delete or rewrite captured workflow history.

See [Privacy and data handling](docs/PRIVACY_AND_DATA.md) for the detailed boundary.

## Local interface security

OpenWorkGraph does not treat `localhost` as authentication.

The normal launcher creates installation-local capability credentials and protects the local interfaces:

- raw/history/export/control API routes require an authenticated dashboard session or local API capability
- desktop collector writes are authenticated so another local process cannot silently inject fabricated workflow history
- the HTTP MCP endpoint requires a separate local bearer capability
- the dashboard uses a launcher bootstrap carried in the URL fragment and exchanges it for an HttpOnly local session; the long-lived API capability is not embedded in dashboard HTML
- the browser sensor uses HMAC challenge-response to verify that the process on port 8787 is the paired OpenWorkGraph installation **before browser evidence is sent**
- browser requests are HMAC-signed with timestamp/nonces and replay protection

If browser authentication fails, the existing extension queue keeps pending evidence rather than sending it to an unverified listener. If port 8787 is already occupied when OpenWorkGraph starts, capture does not start; it will not reuse an unknown localhost service. MCP remains non-critical: if 8788 is unavailable, capture and manual exports continue normally.

These controls materially reduce accidental localhost exposure, unrelated-service probing, naive port squatting and unauthenticated local access. **They are not a security boundary against malware already running with the same operating-system user privileges.** A same-user malicious process may be able to read local credentials or inspect other user-owned resources; stronger protection for that threat model requires operating-system isolation, endpoint security and/or managed enterprise controls.

## What remains intentionally useful

OpenWorkGraph does **not** try to remove every business fact. Depending on what appears in observable titles/labels, the local evidence and rich export can retain things such as:

- company/customer names
- project or deal names
- order/reference numbers
- amounts
- document/page/window titles
- safe UI labels

Those details can be essential for understanding the workflow. They can also be sensitive. **Review a rich export before sharing it outside its intended analysis context.**

## Reliable event delivery

Desktop capture is written locally first and placed into a durable SQLite outbox. If the local API is temporarily unavailable, the same event remains queued and is retried with its original event ID.

The browser sensor has a separate durable queue in extension storage. Browser observations keep stable sensor identity and the work-session identity from capture time so delayed delivery does not silently attach old evidence to a later session. Browser evidence remains queued when the paired local server cannot be authenticated.

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

## Exports for ChatGPT, Claude and data tools

The dashboard exports a captured session as:

- **XLSX** — convenient for ChatGPT/Claude and manual inspection
- **CSV ZIP** — convenient for ChatGPT/Claude and data tools; contains `README_FOR_AI.md`
- **JSON** — full structured representation intended primarily for code/integrations

The dashboard includes **Include rich raw session evidence**. Leave it enabled when detailed reconstruction matters; disable it for a more content-minimized operational export.

CSV/XLSX event tables expose common semantic fields directly, including `action`, `page_host`, `page_path`, `target_label` and `target_role`, while retaining the complete `metadata_json` column for advanced analysis.

The AI data dictionary explains timing fields, stable privacy tokens, capture limits and deliberately retained context. See [Exports and AI analysis](docs/EXPORTS_AND_AI.md).

## MCP: connect your AI to the local context layer

The normal OpenWorkGraph launcher starts an authenticated local Streamable-HTTP MCP endpoint at:

```text
http://127.0.0.1:8788/mcp
```

The endpoint requires the installation's local MCP bearer capability. The dashboard handles this for supported connection flows rather than publishing an unauthenticated localhost endpoint. MCP startup is deliberately non-critical: if the MCP process cannot start, capture, the REST API, dashboard and manual exports continue normally.

The dashboard provides connection help for several client types:

- **Cursor** — one-click MCP install deep-link containing the local endpoint and authorization header, followed by Cursor's native confirmation dialog.
- **Claude Desktop** — guided local MCP configuration using OpenWorkGraph's secured stdio entrypoint and existing private Python runtime; the user does not need to install Python or type Terminal commands.
- **ChatGPT** — guided custom-app / Secure MCP Tunnel setup because ChatGPT's cloud service cannot directly reach a user's localhost endpoint; the local bearer capability must be carried through the supported connector/tunnel configuration.
- **Other MCP clients** — authenticated Streamable-HTTP connection details are available from the authenticated dashboard.

### Rich evidence is the MCP default

OpenWorkGraph's MCP is intentionally **rich-evidence-first**. Context/search/session tools expose a bounded, presentation-redacted slice of raw local evidence by default and place derived context/tasks alongside it. This is the ongoing-access equivalent of manually exporting with **Include rich raw session evidence** enabled, without dumping the entire database into every model call.

Context and workflow tools include:

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

### MCP trust boundary

Page titles, document titles and UI labels are **observed data, not trusted instructions**. Before results cross the MCP boundary, OpenWorkGraph removes invisible direction/control characters, bounds scalar length, suppresses common command-like prompt-injection text and attaches an `_openworkgraph_security` trust annotation.

This MCP hardening is separate from storage-time sensitive-identifier hardening and local bearer authentication. It applies to the copy returned to an AI tool, not to the meaning of the stored workflow event.

## REST data layers

The local `/v1/*` data/control routes below are authenticated in normal v0.48 operation. Browser/collector ingestion uses its own paired/capability-authenticated paths.

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

### Exports and local privacy controls

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

The local HTTP server and auto-start MCP endpoint bind to loopback (`127.0.0.1`). Host/origin restrictions remain in place in addition to the v0.48 capability/pairing controls.

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
| Authenticated REST / MCP | ✅ | ✅ |
| Paired browser sensor | ✅ | ✅ |
| Native control semantics | Accessibility API | Microsoft UI Automation |
| Automated CI | ✅ | ✅ |

Native control semantics are best effort on both platforms. Windows cannot inspect controls from every application, especially when the target application runs at a higher integrity level than OpenWorkGraph or does not expose a UI Automation provider.

## Current limitations

OpenWorkGraph is not yet a finished enterprise product. Current limitations include:

- browser semantic detail requires installation/approval of the optional extension
- some native applications expose weak or no accessibility/UI Automation labels
- activity timing is an estimate: a foreground window is not proof that a person was actively working every second
- inferred tasks/patterns are analytical interpretations, not ground truth
- rich local evidence can still contain sensitive business context even after high-confidence identifier hardening
- AI clients differ in local MCP support; cloud clients may require provider-specific tunnels, app approval or administrator policy
- local capability/pairing controls are not intended to protect against malware already running with the same OS-user privileges
- enterprise-wide RBAC, centrally managed policy, retention/audit controls, encryption policy and fleet deployment are not yet a production control plane

## Documentation

- [Documentation index](docs/README.md)
- [Privacy and data handling](docs/PRIVACY_AND_DATA.md)
- [Exports and AI analysis](docs/EXPORTS_AND_AI.md)
- [Owner/person presentation redaction](docs/OWNER_REDACTION.md)
- [Nontechnical testing guide](NONTECHNICAL_TESTING.md)
- [Standalone macOS launcher notes](docs/STANDALONE_MAC_LAUNCHER.md)

Historical version-specific notes remain in `docs/` for traceability and are marked as historical in the documentation index.

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