# Privacy and data handling

OpenWorkGraph is designed to collect enough workflow evidence for useful process/context analysis without becoming a screen recorder or keylogger.

This document describes the current v0.46 behavior. It is a technical description of the prototype, not a claim that every future enterprise deployment has the same policy requirements.

## Local-first boundary

The current local server binds to loopback (`127.0.0.1`). Captured event data is stored on the local machine unless the user deliberately exports it or later connects OpenWorkGraph to another system.

The local API also applies host/origin restrictions so arbitrary webpages/extensions cannot read work-history endpoints.

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

For old rows that were already irreversibly pseudonymized as payment cards in an earlier version, v0.46 can repair the semantics when the surrounding OCR/reference cue survives. Such a token can be presented as `OCR_REFERENCE_x`; the original digits cannot be reconstructed.

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

This deletes only the local learned-person registry. It does not delete captured work history or alter task/event timing.

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

This is distinct from persistence sanitization. It is intended to reduce the risk that untrusted observed UI text is treated as an instruction by an agent.

## Raw does not mean unsanitized

The phrase **raw local evidence** refers to the richest persisted source-event layer. It does not mean a byte-for-byte copy of everything visible on screen.

High-confidence secrets/identifiers are already hardened before persistence, and intentionally excluded content is omitted. The raw layer is “raw” relative to OpenWorkGraph's normalized/context derivatives.

## Limits

No heuristic privacy system is perfect. False positives and false negatives remain possible, especially in arbitrary visible titles/labels. Enterprise use should therefore add organization-specific policy, authentication, encryption, RBAC, retention, auditability and deployment controls appropriate to the environment.

The current prototype's design goal is a useful local workflow dataset with substantially less invasive collection than screenshots, typed-text capture or recording.
