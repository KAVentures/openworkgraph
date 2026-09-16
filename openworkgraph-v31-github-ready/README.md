# OpenWorkGraph — Workflow Observer v31 rich-evidence rollback build

OpenWorkGraph is a local-first operational data layer for understanding how work actually happens. Workflow Observer captures desktop/browser activity, timing, aggregate effort, and semantic interaction evidence, then exposes structured workflow information through REST/MCP so an enterprise AI can reason over it.


## v31 rollback baseline

v31 deliberately returns to the v27 capture/data behavior after later privacy experiments reduced useful context. Raw local activity evidence is again the primary local source of truth and rich raw evidence is included in exports by default. The normalized operational layer remains available as an optional secondary representation, but it does not replace or hide captured evidence.

No new privacy/redaction logic is inserted into capture in this release. Privacy hardening will be redesigned later as a separate post-processing/export policy so capture fidelity is not sacrificed.

## v27: task-first repeats + immediate navigation

- Brief address-bar navigations are captured directly from the destination URL via browser navigation APIs; no scroll/click is required.
- The dashboard merges browser navigation and desktop interactions into one **Raw activity evidence** feed.
- **Repeated completed tasks** now requires repeated task executions with observed completion actions; repeated site switching alone does not count.
- Navigation fragments remain available only as diagnostic context.
- Repeated-task signatures use normalized action milestones such as `compose → edit → send`.


## v27 design: rich evidence + privacy-safe operational graph

v27 deliberately restores and preserves the **v23 capture behavior** that proved useful in real testing. The capture layer is not privacy-rewritten or weakened.

Every incoming event now produces two local representations:

1. **Raw local evidence** — the rich customer-owned evidence stream used for local verification/debugging. This retains the v23 window/browser/UI context that makes capture understandable.
2. **Normalized operational events** — a second content-minimized stream used by task inference and MCP by default. It keeps work surface, generic action, timing/effort and structure while dropping arbitrary visible text, typed values, subjects, names, document titles, record identifiers and full URL paths.

Examples:

- raw local evidence: `Alice <alice@example.com> — RE: Confidential salary - Gmail`
- normalized event: `Gmail · Reply / Send`

- raw local evidence: `github.com/Acme/Project-Phoenix/issues/48213`
- normalized event: `GitHub · Submit new issue`

The raw layer is not sent through MCP by default. The normalized layer is the intended AI-facing operational graph.

## What capture includes

- application and active-window/tab focus spans
- browser tab/title changes across major desktop browsers
- foreground, engaged, probable-idle and active-input timing
- aggregate keypress counts only — never key identities, key order, or typed text
- global mouse clicks and throttled scrolls
- best-effort macOS Accessibility evidence
- optional WebExtension events: tab activation/navigation, interactive clicks, editor/input focus, form submits, control changes, copy/paste occurrence
- candidate task executions
- repeated task families

## Task inference

Task inference runs on the normalized representation, not arbitrary raw labels. This makes recurring-task matching less dependent on email subjects, recipients, document names or record IDs.

For example, two different fresh emails should both normalize to:

`Gmail → Compose → typing effort → Send → family email.compose_send`

and appear as one repeated family with two executions.

Task labels remain observational suggestions and require contextual review.

## Local API layers

Raw local evidence endpoints:

- `GET /v1/summary?scope=current`
- `GET /v1/events`
- `GET /v1/semantic-activity`
- `GET /v1/sessions/{session_id}`

Privacy-safe operational endpoints:

- `GET /v1/operational-summary?scope=current`
- `GET /v1/operational-events`
- `GET /v1/operational-semantic-activity`
- `GET /v1/operational-sessions/{session_id}`
- `GET /v1/tasks?scope=current`

MCP uses the operational endpoints by default.

## Run on macOS

Use the standalone launcher from the test zip. It installs into:

`~/Library/Application Support/WorkflowObserver`

The local dashboard is:

`http://127.0.0.1:8787`

Run `OPEN_BROWSER_SENSOR_FOLDER.command` after upgrading and reload the unpacked browser extension once.

## Current prototype privacy boundary

The prototype server binds to `127.0.0.1` only. Raw evidence is customer-local and intentionally remains richer than the normalized operational layer. Before enterprise deployment, raw-evidence access should additionally be governed by retention, encryption, RBAC, audit logs and customer policy.

## v27: session export + immediate navigation capture

The local dashboard now includes **Export captured session** controls:

- **JSON** — best for uploading to an AI or analyzing in code.
- **XLSX** — workbook with Overview, effort, tasks, repeated families, transitions, operational events, and semantic activity.
- **CSV ZIP** — the same major tables as separate CSV files.

Exports use the privacy-normalized operational layer by default. A deliberate
**Include raw local evidence** checkbox adds the rich local evidence (which may
contain names, subjects, document titles, URLs, and other sensitive content).

Browser capture also registers visits at document start and through the browser's
committed-navigation API. A brief visit should be recorded even if the user never
scrolls or clicks. Reload the browser extension after upgrading to v27.
