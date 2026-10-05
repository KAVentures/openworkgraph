# OpenWorkGraph

**Open-source, local-first context infrastructure for how work actually happens.**

OpenWorkGraph observes desktop and browser work, preserves privacy-hardened rich evidence locally, and lets authorized AI systems query that evidence through exports, REST, or MCP.

The local product requires **no account and no cloud storage**. Organizations can optionally run the new **OpenWorkGraph Gateway** and PostgreSQL entirely inside infrastructure they control.

[![Tests](https://github.com/KAVentures/openworkgraph/actions/workflows/tests.yml/badge.svg)](https://github.com/KAVentures/openworkgraph/actions/workflows/tests.yml)
[![Latest release](https://img.shields.io/github/v/release/KAVentures/openworkgraph)](https://github.com/KAVentures/openworkgraph/releases/latest)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

> **Project status:** early public infrastructure. The priorities are reconstructable work evidence, strong local defaults, self-hosting, and controlled AI access — not employee productivity scoring.

**AI agents are optional.** OpenWorkGraph's core product records ordinary human desktop/browser workflows on its own. Agent telemetry is an additional evidence source for teams that also want to observe agent execution and human↔agent handoffs.

## Point your AI at the repo

Tell a capable agent:

> Set up https://github.com/KAVentures/openworkgraph and follow AGENTS.md.

For an AI that actually has shell/filesystem access on your **Mac or Windows PC**, the intended experience is autonomous: the agent clones the repo, runs the OS-native bootstrap wrapper (`owg_bootstrap.sh` on macOS or `owg_bootstrap.ps1` on Windows), lets OWG download/provision its own private runtime, installs/starts itself, registers MCP, health-checks the local service, and verifies an OWG context read. No preinstalled system Python is required. You should not need to download files, run terminal commands, edit JSON/TOML, or choose an installer yourself.

The bootstrap stops only at real consent boundaries it cannot safely cross for you, such as macOS Accessibility/Input Monitoring, an AI-access setting you previously turned OFF, or an AI-app restart that cannot be performed from inside the current session. New installs start with AI access ON at Redacted. It never silently enables agent observation or switches AI context to Full.

An unknown local MCP-capable agent receives a generic stdio descriptor and is instructed to register it through its own native MCP mechanism rather than impersonating a known client.

A **remote/cloud** agent must not install the desktop observer in its provider sandbox: that would observe the wrong machine. It is routed to the customer-controlled Gateway's per-person HTTPS /mcp endpoint when the organization has configured Gateway sync and delegated OIDC. The public GitHub repository is not a public relay into a local-only OWG store.

## Why OpenWorkGraph

Most enterprise AI can retrieve what an organization has already written down: documents, email, chat, tickets, CRM records, meeting notes and knowledge bases.

OpenWorkGraph targets a different missing layer:

> **What did people actually do, in what order, across which tools, with what observable effort and information handoffs?**

A rich observed trace might contain:

```text
09:02  Gmail          focus / customer request
09:04  Salesforce     search account
09:05  Google Sheets  pricing sheet
09:07  copy → paste   Sheets → Salesforce
09:10  Salesforce     submit update
09:11  Gmail          send response
```

An AI can reconstruct the workflow from the evidence rather than depending on OpenWorkGraph to permanently decide what the task “means.”

---

# Try the local product

## Recommended: desktop installer

Normal users do **not** need a ZIP, Terminal, PowerShell, or a system Python installation.

### macOS

**[⬇ Download the macOS installer](https://github.com/KAVentures/openworkgraph/releases/latest/download/OpenWorkGraph-macOS.pkg)**

1. Download **OpenWorkGraph-macOS.pkg**.
2. Double-click it and follow the macOS Installer.
3. Open **OpenWorkGraph** from Applications.
4. Approve Accessibility/Input Monitoring if macOS asks.
5. OpenWorkGraph enables **Start at Login** on first launch so observation resumes after normal shutdown/restart cycles. You can turn it off from the OWG menu at any time.

The desktop app includes its own private Python runtime. Its signed/packaged runtime stays inside the application bundle while OpenWorkGraph keeps mutable source, configuration and recorded evidence in the established per-user location under `~/Library/Application Support/WorkflowObserver`. Existing `data/` and `config.json` are preserved across upgrades.

The early-prototype installer produced by the normal open-source release workflow is not yet Developer-ID signed/notarized, so macOS may show an unverified-developer warning. The separate signed-installer workflow can replace the same release download with a notarized build when signing credentials are configured.

### Windows

**[⬇ Download the Windows installer](https://github.com/KAVentures/openworkgraph/releases/latest/download/OpenWorkGraph-Windows-Setup.exe)**

1. Download **OpenWorkGraph-Windows-Setup.exe**.
2. Double-click it and follow Setup.
3. Open **OpenWorkGraph** from the Start menu.
4. The installer enables **Start OpenWorkGraph when I sign in** by default, so observation resumes after normal shutdown/restart cycles unless you opt out during Setup.

The installer includes the private Python runtime and installs per-user under `%LOCALAPPDATA%\OpenWorkGraph`. No system Python installation is required.

The normal open-source release currently produces an unsigned installer, so Windows SmartScreen may ask you to confirm it. The signed-installer workflow can replace the same release download with a code-signed build when signing credentials are configured.

## Advanced / manual install

The release-matched bootstrap scripts remain available for technical users and troubleshooting. They use the same established local installation/data locations.

macOS:

```bash
curl -fsSL https://github.com/KAVentures/openworkgraph/releases/latest/download/install.sh | bash
```

Windows PowerShell:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://github.com/KAVentures/openworkgraph/releases/latest/download/install.ps1 | iex"
```


If you deliberately run the repository from source (for development or demo work), use a virtual environment rather than installing into system Python:

```bash
python -m venv .venv
.venv/bin/python -m pip install -e .
```

On Windows use `.venv\Scripts\python.exe`. This avoids conflicts with unrelated packages already installed in the host environment. The packaged desktop installers and autonomous agent bootstrap already use OWG's private runtime and do not need this step.

## Uninstall / full local-data removal

Removing the desktop application from Applications/Windows Settings removes the normal application entry. OpenWorkGraph's explicit uninstall helpers are the path for also deleting the private runtime, configuration and locally recorded evidence for this user. They require confirmation and refuse to run while port 8787 is in use.

macOS:

```bash
tmp="$(mktemp)" && curl -fL https://github.com/KAVentures/openworkgraph/releases/latest/download/uninstall.sh -o "$tmp" && bash "$tmp"; rm -f "$tmp"
```

Windows PowerShell:

```powershell
$p = Join-Path $env:TEMP "openworkgraph-uninstall.ps1"
Invoke-WebRequest https://github.com/KAVentures/openworkgraph/releases/latest/download/uninstall.ps1 -OutFile $p
powershell -NoProfile -ExecutionPolicy Bypass -File $p
```

The optional browser extension is managed by Chrome/Edge separately; remove it from the browser's Extensions page if you installed it.

## Manual tester ZIPs

The ZIPs remain available as a fallback and for testing. They contain two entry points: **START_OPENWORKGRAPH** starts real local observation; **TRY_DEMO_OPENWORKGRAPH** opens isolated synthetic sample evidence.

### macOS ZIP

**[Download the macOS tester ZIP](https://github.com/KAVentures/openworkgraph/releases/latest/download/OpenWorkGraph-macOS.zip)**

### Windows ZIP

**[Download the Windows tester ZIP](https://github.com/KAVentures/openworkgraph/releases/latest/download/OpenWorkGraph-Windows.zip)**

The ZIP route is no longer the recommended installation path for ordinary users.

---

# Local-first architecture

A normal installation is completely useful by itself:

```text
 Desktop sensor ───────┐
 Browser sensor ───────┤
 Future sensors ───────┤
                       v
          privacy-hardened rich evidence
                       |
                       v
                 local SQLite
                /      |      \
               /       |       \
        dashboard    export    local MCP
```

No OpenWorkGraph account is required.

Evidence is written locally first. The browser extension is optional. Local export and MCP continue to work whether or not any organization Gateway exists.

## Optional organization Gateway

For organizations that want continuous context available to authorized company systems:

```text
Employee endpoint
  local capture + local SQLite
           |
           | optional outbound HTTPS
           | after endpoint-side policy
           v
Customer infrastructure
  OpenWorkGraph Gateway
  customer PostgreSQL
      /            \
   REST             MCP
    |                |
 internal apps    AI/agents
 automation/context systems
```

The Gateway is open source and self-hostable from this same repository. OpenWorkGraph/Kinvectum does **not** have to store or transit the customer's evidence.

See **[Self-hosting](docs/SELF_HOSTING.md)**.

For managed pilots and fleet rollout, use the **[Enterprise Deployment Kit](enterprise/README.md)**. Releases include a machine-wide Windows enterprise installer, MDM scripts, browser policy templates and fleet acceptance checks. Apple/Windows signing credentials and Chrome/Edge store publisher accounts remain external production trust gates; the repository does not pretend to manufacture them.

---

# Rich persisted evidence is canonical

OpenWorkGraph deliberately separates observed evidence from interpretations.

```text
                 canonical rich evidence
                        |
           +------------+------------+
           |            |            |
         AI reads     search       heuristics
           |            |            v
           v            v      task/process hints
   reconstruction    retrieval
```

Deterministic task inference can be useful, but it is not treated as ground truth. A newer model should be able to reinterpret old evidence without the capture layer having thrown away useful detail.

Where older OWG documentation or APIs say **raw evidence**, they mean the richest **persisted privacy-hardened local evidence**. OpenWorkGraph does not expose some hidden pre-privacy event stream. **Redacted** is an additional presentation/AI-sharing transformation on top of the stored privacy-hardened representation.

The canonical AI trace preserves, when observed:

- stable `event_id` provenance;
- timestamp, app/window, event type and source;
- browser hostname/path;
- safe native/browser control role and label;
- tab and browser-session context;
- semantic-action hints plus confidence;
- copy/cut/paste occurrence and transfer linkage;
- foreground, engaged, idle and active-input timing;
- aggregate keypress/click/scroll counts;
- the underlying privacy-hardened event metadata.

Results are bounded and cursor-paginated rather than dumping an entire history into every model call.

---

# What OpenWorkGraph captures

Current capture can include:

- foreground application and window/tab focus spans;
- browser tab activation/navigation when the optional extension is installed;
- foreground, engaged, probable-idle and active-input timing;
- aggregate keypress **counts**;
- global mouse clicks and throttled scrolls;
- best-effort native control metadata through macOS Accessibility / Windows UI Automation;
- browser semantic actions such as clicks, submits and control changes;
- copy/cut/paste **occurrence and linkage**, never clipboard contents.

These human-workflow signals are collected independently of agent observation. You can use OpenWorkGraph entirely for human workflows and never enable an agent integration.

## Deliberately not captured in normal operation

- typed text or ordinary key identities/order;
- clipboard contents;
- password-field values;
- selected text;
- screenshots/screen recording by default;
- browser URL query strings/fragments in structured browser evidence.

**Explicit v0.109 exception:** if the user deliberately enables **Agents → Session continuity → Save visible session messages**, OpenWorkGraph can store the visible user/assistant text from supported local agent sessions in a separate local message table. This is OFF by default, has its own retention and AI/Gateway permissions, never enters the canonical `events` table, and does not include hidden reasoning/thinking, raw provider records or tool-result content.

High-confidence secrets and sensitive identifiers are hardened before persistence where their literal value is not needed. Presentation/export/MCP layers add further protections appropriate to their trust boundary.

Rich business context can intentionally remain when useful — for example project/customer names, amounts, document titles, safe UI labels, and order/reference identifiers. Review evidence before sharing it outside its intended context.

See **[Privacy and data handling](docs/PRIVACY_AND_DATA.md)**.

---

# Workflow discovery for AI implementations

OpenWorkGraph also has an optional **Discovery Mode** for short, purpose-limited workflow studies. A worker can allowlist only the apps/sites relevant to the implementation, run the study for a fixed period, review observed examples and evidence-grounded questions, then explicitly approve a redacted **Discovery Package** for an AI/implementation team. Discovery Mode is additive: when it is off, normal OpenWorkGraph capture and AI/MCP behavior are unchanged.

See **[Discovery Mode](docs/DISCOVERY_MODE.md)**.

---

# AI access

## 1. Export and upload

The dashboard can export JSON, XLSX, or CSV ZIP. The AI guide and starter prompt are bundled with context packages.

For workflow-to-skill use, the **Teach your AI from observed work** flow lets you review the exact example runs before export. **Export redacted evidence** is the recommended sharing path. **Export stored evidence** exports the locally persisted privacy-hardened representation; it is not pre-privacy capture and should be reviewed before external sharing.

OpenWorkGraph supplies the evidence. Your connected AI interprets it and drafts the reusable skill/procedure. OWG's inferred families, dominant steps and support counts remain navigation/descriptive aids rather than task truth.

## 2. Local MCP

Local clients such as desktop AI tools and IDE agents normally use MCP over **stdio**:

```text
AI application
     |
     | MCP / stdio
     v
OpenWorkGraph MCP
     |
     v
authenticated local API
     |
     v
local SQLite
```

No separate MCP network port is kept open for normal local use.

AI access starts **ON** on a new installation, with **Redacted** context. If the person turns AI access OFF, that choice is remembered across restarts unless they later change it; the optional Privacy setting can also reset AI access OFF on every restart. By default AI apps get **Redacted** context: titles and labels keep their meaning, but people, emails, phone numbers, personnummer and long IDs become stable tokens (`Re: Contract for PERSON_1A2B3C - Gmail`). You can switch to **Full** under Connect → Connections → *AI context detail*; an organization can lock it to Redacted. Every MCP response states its `detail_level`. See [Privacy and data](docs/PRIVACY_AND_DATA.md#three-layers-raw-redacted-safe-allowlist).

The local MCP boundary also treats observed page/window/UI text as untrusted data and suppresses instruction-like prompt-injection content in the copy returned to the model.

New dashboard-generated connections and the Claude MCP bundle use a compact **13-tool** surface so agents have fewer overlapping choices. For turning observed work into a skill, the intended path is deliberately clear: use `find_repeated_workflows` only to discover candidate examples, select the concrete runs that really belong together, call `get_workflow_evidence` for those execution IDs, and use `get_workflow_trace` only when deeper canonical drill-down is needed. The external AI then drafts the procedure and asks for business rules, source-of-truth choices, escalation criteria or approval boundaries that observation cannot establish.

`get_workflow_evidence` does **not** make support counts, repeated behavior, Playbooks or inferred families authoritative. Repetition is not policy or permission, clipboard contents were not captured, and an AI should prefer current authorized APIs/connectors/tools over literal click replay when they can safely produce the same outcome.

Existing saved configurations that explicitly launch `mcp_server.secure_stdio` keep the legacy 24-tool compatibility surface; OpenWorkGraph does not silently remove existing tool names underneath configured clients.

The canonical evidence tool remains:

```text
get_workflow_trace(...)
```

It exposes rich-but-paginated observed evidence. Other summary/task tools are optional indexes and should point back to source evidence when a conclusion matters.

Normative governance capabilities remain implemented and tested but are experimental rather than part of the default compact MCP menu. Set `OWG_EXPERIMENTAL_GOVERNANCE=1` to expose the experimental compact governance tools. The flag does not unmount existing REST routes or turn observed repetition into policy or permission. See **[Experimental governance](docs/EXPERIMENTAL_GOVERNANCE.md)**.

## 3. Gateway REST / MCP

Backend products do not need MCP. They can call the customer-hosted Gateway REST API directly.

AI/agent frameworks can instead use the Gateway MCP adapter. Both interfaces query the same organization-scoped evidence service.

See **[How MCP works](docs/MCP_ARCHITECTURE.md)** and **[Integration patterns](docs/INTEGRATIONS.md)**.

---

# Self-host the organization Gateway

The included deployment uses PostgreSQL and Docker Compose:

```bash
git clone https://github.com/KAVentures/openworkgraph.git
cd openworkgraph
cp deploy/.env.example deploy/.env
# replace every placeholder in deploy/.env with independent random secrets

docker compose --env-file deploy/.env -f deploy/docker-compose.yml up -d --build
```

Then enroll an approved endpoint:

```bash
python -m connector.enroll \
  --gateway https://openworkgraph.company.internal \
  --organization acme \
  --actor alice \
  --enrollment-token '<enrollment secret>'
```

Gateway sharing can be paused without stopping local capture:

```bash
python -m connector.control pause
python -m connector.control status
python -m connector.control resume
```

Endpoint local policy is enforced before transmission. Organization policy can further restrict sharing but cannot broaden endpoint restrictions.

See **[Self-hosting](docs/SELF_HOSTING.md)** for production notes and integration-token setup.

---

# Gateway security model

v0.53 intentionally separates identities:

- **device credentials** can write evidence for the authenticated endpoint and read organization sharing policy;
- **integration credentials** can read only explicitly granted scopes;
- **admin/enrollment bootstrap secrets** are setup/control capabilities, not routine integration credentials.

The Gateway stores hashes of device/service tokens. Organization, actor and device identity are taken from the authenticated endpoint credential rather than trusted from uploaded JSON. Evidence event identity is scoped by organization. Reads are tenant-scoped and audited without logging search terms/evidence into the audit record by default.

Current integration read scopes:

```text
evidence:read
context:read
transfers:read
agent-sessions:read
```

`agent-sessions:read` is a separate optional content scope. It does not follow from `evidence:read` or `context:read`, and the organization must also explicitly allow agent-session messages.

Administrators use named accounts at `https://<your-gateway>/admin`: password plus authenticator app, or company sign-in (OpenID Connect).

- **Roles:** owner, admin or read-only viewer.
- **Employees:** a roster, with personal invitations that tie each computer to one employee.
- **Employee view:** each employee can see what the Gateway holds about them, and every recorded read, at `/me`.

See [docs/ORGANIZATION_ROLLOUT.md](docs/ORGANIZATION_ROLLOUT.md).

---

# REST interface

Local endpoints remain authenticated in normal operation. The self-hosted organization Gateway exposes a separate organization-scoped API.

Core Gateway endpoints:

```text
GET  /v1/capabilities
GET  /v1/workflow-trace
POST /v1/search
GET  /v1/context/current
GET  /v1/transfers
```

`/v1/workflow-trace` is the canonical evidence endpoint.

---

# Reliable delivery

Local desktop capture uses a durable queue into the local API. Browser evidence has its own extension queue.

Organization synchronization is independent of both. It reads the canonical local event database and advances its durable cursor only after the Gateway acknowledges the complete shareable batch. Retries are idempotent by organization-scoped event ID.

Optional visible agent-session messages use a **separate** synchronization channel, cursor and permission set. They are never enabled by structural `allow_agent_events`; endpoint opt-in (`allow_gateway_session_messages`), organization policy (`allow_agent_session_messages`) and a device credential carrying `agent-sessions:write` must all agree. Existing pre-v0.109 device credentials are not silently broadened and must be explicitly re-enrolled/rotated before transcript upload is possible. Organization retention and lifecycle purge apply to shared session messages as well as canonical evidence.

If the current organization policy cannot be fetched, synchronization fails closed rather than sharing under a potentially broader fallback policy.

---

# What OpenWorkGraph is not

OpenWorkGraph is not intended to produce an employee “productivity score” or silently judge individual performance.

The useful organizational object is the **workflow/process evidence**: what steps occur, where effort accumulates, how tools are crossed, where information moves, which exception paths recur, and what an authorized AI could potentially improve or automate.

Deployers remain responsible for applicable workplace/privacy law, transparency, purpose limitation, retention and access controls.

---

# Development

Python 3.11+:

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
```

Gateway/PostgreSQL development extras:

```bash
python -m pip install -e ".[gateway,dev]"
```

The CI matrix tests Linux, macOS and Windows, browser JavaScript, and builds the self-hosted Gateway container. Pull requests also build the standalone macOS/Windows packages and local MCP bundle before merge.

---

# Documentation

- [Self-hosting](docs/SELF_HOSTING.md)
- [How MCP works](docs/MCP_ARCHITECTURE.md)
- [Experimental governance](docs/EXPERIMENTAL_GOVERNANCE.md)
- [Integration patterns](docs/INTEGRATIONS.md)
- [Privacy and data handling](docs/PRIVACY_AND_DATA.md)
- [Exports and AI](docs/EXPORTS_AND_AI.md)
- [Nontechnical testing](NONTECHNICAL_TESTING.md)

---

# License

Apache License 2.0. See [LICENSE](LICENSE).

Copyright 2026 Koyar Afrasyab (Kinvectum). The software license does not grant rights to project branding.
