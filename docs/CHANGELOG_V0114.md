# OpenWorkGraph v0.114.0

## Configurable browser context without weakening the privacy-first path

v0.114 adds an explicit privacy model for linking observed browser work to the business objects it operates on. The feature is additive: business-object reference capture remains off by default, existing browser/desktop evidence continues to work, and no content connector or duplicate company-data store is introduced.

### Three browser-context privacy profiles

- **Privacy-first** keeps business-object references off. It also masks known object-ID positions in stored Google Docs/Drive, GitHub, Salesforce, Jira and Linear paths before persistence. This is intentionally stricter than the pre-v0.114 URL-path behavior.
- **Context** recognizes allowlisted objects and keeps only an installation-keyed local correlation token. Provider record/thread/document identifiers are not retained.
- **Rich enterprise** is an explicit opt-in that may retain the minimal validated provider-specific locator needed for an authorized connector or AI to resolve the object. It does not enable unrelated optional sensors.

The same underlying switches remain individually configurable, so organizations and local users can choose a custom combination instead of a preset.

### Allowlisted business-object recognition

The first reference parsers cover:

- Google Docs, Sheets, Slides and Drive files;
- Gmail web conversation locators on known conversation routes;
- GitHub pull requests and issues;
- Salesforce records;
- Jira issues; and
- Linear issues.

Unknown sites and sensitive-looking arbitrary routes are not guessed. They fall back to the existing sanitized browser evidence.

### Two keyed persistence boundaries

Context correlation tokens are no longer plain hashes of provider IDs.

1. Before a resource-reference event can enter the browser extension's durable retry queue, the extension HMACs the canonical allowlisted reference with the installation browser-pairing secret. The queue therefore contains an opaque `owg:e:…` sensor fingerprint rather than a dictionary-attackable hash of a short Jira key or PR number.
2. Before local event persistence, the server replaces that sensor fingerprint with a different `owg:r:…` HMAC keyed by the installation API secret. The raw browser fingerprint is not stored in the canonical event row.

Rich enterprise mode additionally validates that a supplied locator and browser fingerprint agree. Re-hardening an already persisted local token is idempotent.

These local capability secrets remain subject to the existing same-operating-system-user threat-model limitation described in `PRIVACY_AND_DATA.md`.

### Existing URL privacy remains in force

This release does **not** add storage of:

- full browser URLs;
- URL query values or fragments;
- typed text;
- clipboard contents;
- page contents;
- password-field values; or
- arbitrary filenames/file contents.

Known resource references are extracted only through the allowlist before generic URL sanitization removes their identifier. Host/title exclusion policy is checked before a Rich enterprise locator can enter the extension retry queue, and the server independently applies the policy again at ingest.

### Browser sensor update notice

The browser extension is v1.14.0 for this release. The local server already reports the expected and observed sensor versions; the dashboard now turns a mismatch into an explicit **Browser sensor update available** notice telling unpacked-extension users to reload it. Existing capture continues while the old sensor is running, but v0.114 browser-context features remain unavailable until the sensor is current.

### Compatibility

- No canonical event schema is replaced.
- Existing desktop capture, browser semantic capture, agent observation, MCP tools and Gateway data model remain intact.
- The resource-reference path is opt-in and uses ordinary privacy-hardened browser events.
- Native macOS/Windows active-URL capture is not part of v0.114; the browser extension remains the richer browser-semantic source.
- No Google Workspace, Microsoft 365, Salesforce or other content-ingestion connector is added.
