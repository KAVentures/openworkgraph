# OpenWorkGraph security and privacy boundaries

OpenWorkGraph is an early local-first prototype for observing work context and workflow structure. This document describes what the current security/privacy controls do, and what they do **not** guarantee.

## Data collection model

OpenWorkGraph captures application/window focus, browser navigation/context, aggregate keyboard effort, clicks/scrolls, and best-effort semantic control metadata. It deliberately does not capture typed key identities/order or clipboard contents.

Browser/native control labels are intended to capture short control semantics, not page/message bodies. Long row/body-like labels are dropped rather than retained as control labels.

Private/incognito browser operation is disabled for the browser extension. Desktop and browser heartbeat/event exclusions are intended to follow the same configured exclusion rules.

## Pre-storage minimization

Before desktop events are written to the collector recovery JSONL, durable outbox or main event database, OpenWorkGraph applies narrow high-confidence minimization for literals that are not needed to understand a workflow.

Current classes include:

- Swedish personnummer / samordningsnummer
- labelled patient, journal, case and account identifiers
- labelled bank and organization identifiers
- mod-97-valid IBANs
- Luhn-valid payment-card numbers
- common credential/token shapes and explicit secret/token/password assignments
- URL query/fragment values and token-like URL path segments on the browser path

These values are pseudonymized/minimized while preserving event structure and useful workflow semantics. OpenWorkGraph intentionally does **not** generically erase salaries, ordinary business amounts, project/deal names, company/counterparty names, order descriptions or other useful organizational context.

Legacy collector JSONL/outbox data is migrated through the same identifier sanitizer. Recovery JSONL is bounded/rotated rather than growing indefinitely.

## Presentation identity masking

Additional name/person masking happens when data is presented through dashboard/API/export views. The local user's strong detected/configured identity is shown as `OWNER`; other high-confidence people use `PERSON` / stable `PERSON_x` tokens where applicable.

Presentation masking is distinct from pre-storage minimization. Rich local evidence is therefore privacy-minimized, not byte-for-byte raw.

## MCP trust boundary

Observed page titles, UI labels, task/resource titles and similar strings are treated as **untrusted data** when returned through MCP. The MCP boundary strips invisible control/bidi characters, bounds scalar length, suppresses command-like prompt-injection text, and adds `_openworkgraph_security` metadata instructing downstream models not to treat observed text as instructions or authorization.

This protection applies to the MCP copy. It does not rewrite the local evidence database.

## Screenshots

Screenshot capture is off by default. If `screenshots_enabled` or `interaction_screenshots_enabled` is explicitly enabled, the current implementation can save full-monitor JPEGs for non-excluded activity.

Screenshot pixels do not pass through the text redaction/pseudonymization pipeline and may contain unrelated visible content from other windows. Screenshot mode should be treated as a high-risk diagnostic feature and kept disabled unless specifically required.

## Local API threat model

The API binds to `127.0.0.1` and uses trusted-host/origin restrictions so ordinary remote webpages/browser origins cannot read the work-history API.

Current limitation: a process running locally as the same user can make no-Origin requests to localhost read endpoints. There is not yet a mandatory per-install read capability token. Therefore loopback binding should not be treated as a security boundary against malicious same-user local software.

## Browser sensor/server trust

The browser extension posts to the local OpenWorkGraph endpoint on `127.0.0.1:8787`.

Current limitation: the extension does not yet cryptographically authenticate which process owns that port. A future design should use pairing/challenge-response or equivalent mutual authentication without silently breaking browser sensor connectivity. Until then, do not describe the browser-to-server loopback path as protected against a malicious same-user process that can bind the port.

## Local pseudonymization keys

Local pseudonymization keys are permission-restricted to the current user where supported. They currently remain in the OpenWorkGraph local data area rather than macOS Keychain / Windows DPAPI.

This preserves stable pseudonyms across upgrades. Moving existing keys into OS-backed secret storage should be done only with a migration that preserves the exact existing key bytes; generating new keys would silently change historical `PERSON_x` / identifier tokens.

Avoid placing the OpenWorkGraph data directory inside automatically synced folders such as Dropbox, OneDrive or iCloud Drive unless that is an intentional deployment choice.

## Exports

The normalized operational export is lower-content. The optional rich evidence export contains more local context for reconstruction, but still reflects the pre-storage minimization described above.

Privacy filtering reduces risk; it does not make workflow evidence anonymous. Exported data can still contain sensitive business/resource/page semantics and should be handled accordingly.

## Deferred hardening

The following are intentionally not forced on by default yet because a poorly designed rollout could break core functionality:

1. mandatory capability-token authentication for all localhost read endpoints;
2. mutual authentication/pairing between browser extension and local server;
3. migration of existing pseudonymization keys into Keychain/DPAPI;
4. screenshot image-redaction/focused-window redesign;
5. blanket exclusion of terminal applications, because terminals are important workflow surfaces for many users.

These are known limitations, not forgotten guarantees. Changes in these areas should preserve dashboard, MCP, export and browser-sensor compatibility and should ship with migration/failure-mode tests.
