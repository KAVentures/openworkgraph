# OpenWorkGraph — nontechnical testing

The easiest test path is the standalone GitHub Release ZIP for your platform.

## Start OpenWorkGraph

### macOS

1. Download `OpenWorkGraph-macOS.zip` from the latest GitHub Release.
2. Unzip it.
3. Right-click `START_OPENWORKGRAPH.command` → **Open**.
4. Confirm **Open** if macOS asks.
5. Approve Accessibility/Input Monitoring if requested.
6. The local dashboard should open automatically.

### Windows

1. Download `OpenWorkGraph-Windows.zip` from the latest GitHub Release.
2. Unzip it.
3. Double-click `START_OPENWORKGRAPH.cmd`.
4. Review/accept any security warning only if you trust this repository.
5. The local dashboard should open automatically.

No manual Python installation is required.

## Add the browser sensor

Run the included `ADD_BROWSER_SENSOR` helper after OpenWorkGraph has started once, then load the opened `browser_extension` folder as an unpacked extension in Chrome/Edge.

The browser sensor is important: without it, OpenWorkGraph can see the browser application but cannot reliably distinguish Gmail, Google Docs, ChatGPT, Salesforce and other individual browser tools.

The current extension explicitly does **not** run in private/incognito windows.

## Core checks

### 1. Brief navigation must appear immediately

Open a new tab, type a URL, press Enter, stay on the page for only 1–2 seconds, and switch away **without scrolling or clicking**.

Expected: Raw/Rich activity evidence or browser semantic activity contains the visited site. The visit must not depend on a later scroll or click.

### 2. Browser work surfaces should stay separate

Use several browser tools such as Gmail, Google Docs, ChatGPT or another web app.

Expected: work-surface views attribute effort to the logical browser surface rather than collapsing everything into `Google Chrome`.

### 3. Rich evidence remains understandable without retaining high-risk literals

Work normally for a few minutes.

Expected: Rich activity evidence should still make it possible to reconstruct the work, but selected high-risk literals are minimized before persistence. Examples include URL query/fragment secrets, token-like URL path segments, personnummer/patient/case/account IDs, validated IBAN/card numbers and credential-shaped secrets.

Ordinary business context such as project names, company names and normal amounts should remain available unless another privacy rule applies.

### 4. Long clickable rows must not become message/body capture

Click a normal short button such as **Send** or **Create repository**.

Expected: the short semantic label is retained.

Then click a long mail/message/record row whose visible text contains a whole subject/body or many fields.

Expected: OpenWorkGraph records the click/action and surrounding page/work-surface context, but does not treat the entire long row body as the control label.

### 5. Excluded/private browser pages must not leak through heartbeat status

Visit a page matching an exclusion rule (for example a configured banking/private pattern).

Expected: the browser sensor may remain connected, but the excluded page's hostname/title/work-surface must not appear through the live browser status. Previous page identity must not be carried forward as though it were still active.

### 6. Repeated completed tasks should not equal repeated navigation

Bounce between two websites several times without completing a meaningful action.

Expected: this may appear as a navigation fragment, but it must not become a repeated completed task merely because the same site sequence occurred more than once.

Then perform a genuinely repeatable completed task twice, such as composing and sending two fresh emails.

Expected: separate task executions should be inferred, and the repeated task family should count both executions.

### 7. Delivery should survive temporary interruption

With OpenWorkGraph running, briefly interrupt the local API/observer process or otherwise create a short delivery failure, then restore it.

Expected: queued desktop/browser observations should be delivered after recovery rather than silently disappearing. Desktop JSONL/outbox persistence should contain the privacy-minimized form of sensitive identifiers, not a less-protected copy.

### 8. Exports

Capture a few minutes of work, then export the session as JSON and XLSX.

Test both:

- normalized/operational export without rich evidence
- deliberate export with rich evidence included

Expected: the normalized representation keeps task/process structure while dropping unnecessary content; the rich export preserves enough evidence for detailed reconstruction while retaining the same pre-storage minimization of high-confidence identifiers/credentials.

### 9. MCP prompt-injection boundary

If you use the MCP server, create or visit a harmless test page whose title contains an instruction-like string such as `SYSTEM: ignore previous instructions`.

Expected: local evidence can still record the observed title, but MCP output must treat it as untrusted data and suppress instruction-like content rather than passing it through as a model instruction.

## Screenshots

Screenshots are off by default. Keep them off for ordinary testing. If explicitly enabled, current screenshot capture is a high-risk diagnostic feature: screenshot pixels do not receive the text redaction/pseudonymization pipeline.

## Current prototype limitations to understand while testing

- Same-user local programs are not yet required to authenticate to localhost read endpoints.
- The browser extension does not yet cryptographically authenticate the process that owns port `8787`.
- Local pseudonymization keys are permission-restricted where supported but are not yet migrated into macOS Keychain / Windows DPAPI.

These limitations are documented so testers do not mistake `local-first` for a complete malware/host-compromise security boundary. See `SECURITY.md` for details.

## Useful AI test

Give the exported session to ChatGPT, Claude or another model and ask:

> What repeated work do you see? What appears inefficient? What internal tool or automation would help most? Show the observed evidence behind each suggestion.

The goal is not that OpenWorkGraph itself must make every conclusion. The goal is to produce a sufficiently accurate, reconstructable work dataset that another AI can reason over.
