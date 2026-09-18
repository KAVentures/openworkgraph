# Privacy and data handling

OpenWorkGraph is designed to collect enough workflow evidence for useful process/context analysis without becoming a screen recorder or keylogger.

This document describes the current v0.48 behavior. It is a technical description of the prototype, not a claim that every future enterprise deployment has the same policy requirements.

## Local-first boundary

The current local server binds to loopback (`127.0.0.1`). Captured event data is stored on the local machine unless the user deliberately exports it or connects OpenWorkGraph to another system.

Loopback binding alone is not treated as authentication. v0.48 adds installation-local capability controls around the local interfaces:

- raw/history/export/control API reads require either an authenticated dashboard session or the installation API capability token
- desktop collector writes are authenticated with the installation API capability
- Streamable-HTTP MCP requires a separate local MCP bearer capability
- the browser sensor proves the local OpenWorkGraph server knows its pairing secret before browser evidence is sent
- browser sensor requests are HMAC-signed with timestamp/nonces so unauthenticated local processes cannot inject arbitrary browser events without the pairing secret
- repeated browser request signatures are rejected to reduce replay

The long-lived API/MCP/pairing secrets are generated locally and stored under the installation auth directory. On POSIX systems OpenWorkGraph attempts to enforce `0700` on the auth directory and `0600` on secret files. On Windows the files inherit the current user's application-data ACLs.

### Dashboard authentication

The dashboard does **not** receive the long-lived API token in its HTML.

The launcher opens the dashboard with a random bootstrap secret in the URL fragment (`#...`). URL fragments are not sent with the initial HTTP request. The dashboard exchanges that launcher-only value for an HttpOnly, SameSite local session cookie and removes the fragment from the visible URL state.

An unauthenticated process can still fetch the non-sensitive dashboard shell, but data-bearing `/v1/*` routes fail closed until the caller presents a valid capability/session.

### Browser server authentication

The browser extension no longer sends rich page/UI evidence merely because something is listening on `127.0.0.1:8787`.

Before an authenticated browser API request, the extension sends a random challenge. Genuine OpenWorkGraph returns an HMAC proof derived from the installation browser-pairing secret. The extension verifies that proof locally before sending the actual event. The event request itself is then HMAC-signed and replay-protected.

Normal standalone installs write a local `browser_extension/pairing.json` credential at runtime before the unpacked extension is used. That file is generated locally and is not part of the repository/release. If automatic pairing is lost, the dashboard can issue a short-lived one-time 8-digit recovery code and the extension popup can pair again. Repeated wrong attempts invalidate the code.

If authentication fails, the extension's existing durable queue retains pending events rather than sending them to an unverified localhost service.

## Important threat-model limit

These controls raise the bar substantially over an unauthenticated localhost port and protect against accidental localhost probing, unrelated local services, naive port squatters, misconfigured tools/proxies, and processes that do not possess the installation credentials.

They are **not** intended to protect against malware already running with the same operating-system user privileges. A same-user malicious process may be able to read local credential files, inspect process state, or access other resources owned by that user. Stronger protection against that class of attacker requires operating-system isolation, privilege separation, endpoint security, and/or enterprise deployment controls.

"Local" therefore means local storage/network scope by default; it should not be interpreted as an absolute confidentiality boundary against code already executing as the same user.

## What OpenWorkGraph captures

Current signals include:

- active application/window focus spans
- browser tab activation and navigation when the optional browser sensor is installed
- foreground, engaged, probable-idle and active-input timing
- keypress counts
- global clicks and throttled scrolls
- safe native control identity/label metadata through macOS Accessibility or Windows UI Automation, best effort
- browser semantic events such as interactive clicks, editor/input focus, form submit and control change
- occurrence of copy/paste
- derived task/process/effort structures

## What is deliberately not captured

Normal operation does not store:

- typed text
- individual key identities or key order
- clipboard contents
- password-field values
- selected text
- screenshots or screen recordings by default
- browser URL query strings or fragments

The keyboard sensor reports counts/timing only.

Native accessibility/UI Automation code deliberately avoids value/text patterns that would expose field contents.

## Screenshots

Screenshot capture exists as disabled code/config support but is disabled in the normal configuration and is not part of the current product direction or standard exports. The current privacy strategy assumes useful workflow inference should work without continuous screenshots or recording.

## Storage-time sensitive-identifier hardening

Before event evidence is written to local persistence, the sensitive-identifier sanitizer pseudonymizes or removes high-confidence values whose literal form is not needed for workflow analysis.

Current classes include:

- Swedish personal identifiers
- explicitly labelled patient/journal/case/account identifiers
- IBANs
- recognized payment-card numbers
- credential-shaped secrets
- secret-bearing environment variables/config assignments
- bearer tokens
- passwords embedded in connection strings
- common API/token formats
- full PEM private-key blocks

Tokens are installation-local stable pseudonyms such as:

- `PERSONNUMMER_x`
- `PATIENT_ID_x`
- `CASE_ID_x`
- `IBAN_x`
- `PAYMENT_CARD_x`
- `SECRET_x`

A long Luhn-valid number that is sensitive-looking but cannot be confidently classified as a card can be masked as `SENSITIVE_NUMBER_x` rather than being exposed or falsely described as a card.

## Swedish OCR/reference numbers

Luhn validity alone is not sufficient evidence that a number is a payment card. Swedish OCR references and many other business identifiers also use check digits.

OpenWorkGraph therefore gives explicit non-card/reference cues precedence over card heuristics. Context such as `OCR`, `OCR-nummer`, `referensnummer`, `betalningsreferens`, invoice/customer/tracking cues and similar language prevents a number from being falsely represented as `PAYMENT_CARD_x`.

For old rows that were already irreversibly pseudonymized as payment cards in an earlier version, v0.46+ can repair the semantics when the surrounding OCR/reference cue survives. Such a token can be presented as `OCR_REFERENCE_x`; the original digits cannot be reconstructed.

## Person/owner presentation redaction

Person-name handling is a separate presentation policy rather than the same storage sanitizer.

When data is returned through dashboard/API/MCP/export surfaces, high-confidence people can be shown as `PERSON_x`, ambiguous first names as `PERSON`, and the local user as `OWNER`.

Persistent person-name learning is intentionally conservative. Strong evidence such as `Name <email>`, sender/recipient fields, `from`, `cc`, `bcc`, `reply to`, `meeting with`, `call with`, or another person-specific field can teach an alias.

Generic workflow language and status/team phrases are rejected. A phrase such as `Transition to In Progress` or `Meeting with Legal Team` should not create a persistent fake person.

See [OWNER_REDACTION.md](OWNER_REDACTION.md) for details.

## Reset learned person aliases

The dashboard exposes **Reset learned person aliases**, backed by:

```text
POST /v1/privacy/reset-learned-names
```

This control is authenticated in v0.48. It deletes only the local learned-person registry. It does not delete captured work history or alter task/event timing.

## Deliberately retained workflow context

OpenWorkGraph is not designed to erase every business fact. The following may remain in rich local evidence when observable because they can be necessary for process understanding:

- company/customer names
- project/deal names
- order/reference numbers
- amounts
- document/page/window titles
- safe UI labels

This is a deliberate utility/privacy tradeoff. A rich export should be reviewed before it is shared beyond the intended analysis context.

## Excluded applications/pages

Configured excluded applications/title patterns and excluded browser contexts are handled specially. Excluded rows can retain timing/activity evidence while omitting sensitive content labels/titles.

This lets broad effort timing remain useful without preserving content from deliberately excluded surfaces.

## Browser URL handling

Browser evidence removes query strings and fragments. Token-like or identifier-like path segments are normalized where possible. Authentication/recovery/invite/token-related path segments receive additional sanitization.

## MCP trust boundary

Observed titles and labels are data, not instructions to the AI.

Before results cross the MCP boundary, OpenWorkGraph applies additional output hardening such as:

- removal of invisible direction/control characters
- scalar length limits
- suppression of common command-like prompt-injection text
- `_openworkgraph_security` trust annotation

The HTTP MCP endpoint is also capability-protected in v0.48. Local clients such as Cursor receive the bearer capability through the authenticated dashboard connection flow; stdio clients such as Claude Desktop can launch the local secured stdio entrypoint directly.

This is distinct from persistence sanitization. It is intended to reduce the risk that untrusted observed UI text is treated as an instruction by an agent while preventing an unauthenticated localhost process from using MCP as an alternate read path.

## Raw does not mean unsanitized

The phrase **raw local evidence** refers to the richest persisted source-event layer. It does not mean a byte-for-byte copy of everything visible on screen.

High-confidence secrets/identifiers are already hardened before persistence, and intentionally excluded content is omitted. The raw layer is “raw” relative to OpenWorkGraph's normalized/context derivatives.

## Limits

No heuristic privacy system is perfect. False positives and false negatives remain possible, especially in arbitrary visible titles/labels. Enterprise use should therefore add organization-specific policy, encryption at rest where required, RBAC, retention, auditability, endpoint controls and managed deployment appropriate to the environment.

The current prototype's design goal is a useful local workflow dataset with substantially less invasive collection than screenshots, typed-text capture or recording, plus explicit local authentication rather than relying on loopback binding alone.
