# OpenWorkGraph

**Open-source, local-first context infrastructure for how work actually happens.**

OpenWorkGraph observes desktop and browser work, preserves privacy-hardened rich evidence locally, and lets authorized AI systems query that evidence through exports, REST, or MCP.

The local product requires **no account and no cloud storage**. Organizations can optionally run the new **OpenWorkGraph Gateway** and PostgreSQL entirely inside infrastructure they control.

[![Tests](https://github.com/KAVentures/openworkgraph/actions/workflows/tests.yml/badge.svg)](https://github.com/KAVentures/openworkgraph/actions/workflows/tests.yml)
[![Latest release](https://img.shields.io/github/v/release/KAVentures/openworkgraph)](https://github.com/KAVentures/openworkgraph/releases/latest)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

> **Project status:** early public infrastructure. The priorities are reconstructable work evidence, strong local defaults, self-hosting, and controlled AI access — not employee productivity scoring.

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

## macOS

**[⬇ Download the latest macOS tester ZIP](https://github.com/KAVentures/openworkgraph/releases/latest/download/OpenWorkGraph-macOS.zip)**

1. Download and unzip the ZIP.
2. Right-click **`START_OPENWORKGRAPH.command` → Open**.
3. Confirm **Open** if macOS asks.
4. OpenWorkGraph installs its own private runtime; no system Python is required.
5. Approve Accessibility/Input Monitoring if requested.
6. The authenticated local dashboard opens at `http://127.0.0.1:8787`.

For richer browser-native context, run **`ADD_BROWSER_SENSOR.command`** once and load the opened extension folder.

## Windows

**[⬇ Download the latest Windows tester ZIP](https://github.com/KAVentures/openworkgraph/releases/latest/download/OpenWorkGraph-Windows.zip)**

1. Download and unzip the ZIP.
2. Double-click **`START_OPENWORKGRAPH.cmd`**.
3. Review any Windows security warning for this early unsigned prototype.
4. OpenWorkGraph installs its own private runtime; no system Python is required.
5. The local dashboard opens at `http://127.0.0.1:8787`.

For richer Chrome/Edge context, run **`ADD_BROWSER_SENSOR.cmd`** once.

## One command

macOS:

```bash
curl -fsSL https://raw.githubusercontent.com/KAVentures/openworkgraph/main/install.sh | bash
```

Windows PowerShell:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/KAVentures/openworkgraph/main/install.ps1 | iex"
```

Inspect remote scripts before executing them if that is your security policy.

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

---

# Raw rich evidence is canonical

OpenWorkGraph deliberately separates observed evidence from interpretations.

```text
                 canonical rich evidence
                        |
           +------------+------------+
           |            |            |
         AI reads     search       heuristics
           |            |            |
           v            v            v
   reconstruction    retrieval   task/process hints
```

Deterministic task inference can be useful, but it is not treated as ground truth. A newer model should be able to reinterpret old evidence without the capture layer having thrown away useful detail.

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

## Deliberately not captured in normal operation

- typed text or ordinary key identities/order;
- clipboard contents;
- password-field values;
- selected text;
- screenshots/screen recording by default;
- browser URL query strings/fragments in structured browser evidence.

High-confidence secrets and sensitive identifiers are hardened before persistence where their literal value is not needed. Presentation/export/MCP layers add further protections appropriate to their trust boundary.

Rich business context can intentionally remain when useful — for example project/customer names, amounts, document titles, safe UI labels, and order/reference identifiers. Review evidence before sharing it outside its intended context.

See **[Privacy and data handling](docs/PRIVACY_AND_DATA.md)**.

---

# AI access

## 1. Export and upload

The dashboard can export JSON, XLSX, or CSV ZIP. The AI guide and starter prompt are bundled with context packages.

Use rich raw evidence when accurate reconstruction matters. Derived summaries/tasks are convenience views.

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

AI access starts OFF on every OpenWorkGraph launch. The local MCP boundary also treats observed page/window/UI text as untrusted data and suppresses instruction-like prompt-injection content in the copy returned to the model.

New dashboard-generated connections and the Claude MCP bundle use a compact eight-tool surface so agents have fewer overlapping choices: current context, search, canonical trace, work profile, repeated workflows, task context, descriptive feedback from similar runs, and agent-run inspection. Existing saved configurations that explicitly launch `mcp_server.secure_stdio` keep the legacy 24-tool surface; OpenWorkGraph does not silently remove those tools underneath existing clients.

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
```

For larger deployments the Gateway can sit behind customer-controlled OAuth/OIDC/SSO infrastructure. Native enterprise identity provisioning is layered separately from the core data plane.

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
