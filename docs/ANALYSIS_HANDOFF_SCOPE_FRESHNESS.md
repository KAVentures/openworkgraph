# AI evidence handoff: exact-period freshness

The local **Prepare for AI** action is a user-initiated preview and export, not an
AI invocation or a grant of new source access. It uses the existing authenticated
`/v1/analysis-preview` and `/v1/export-ticket` endpoints and retains their
privacy/redaction defaults.

## Why a freshness guard is necessary

The work period can change while a preview is awaiting local authentication or
an API response. Without a per-click freshness check, an older **Prepare for
AI** click could open a modal containing the newer period's shared preview.
Likewise, a modal prepared for one period could remain visible while the
underlying period changes, leaving its copy/download buttons pointing to an
outdated selection. These are provenance and consent-clarity failures, even
though the existing backend still applies its authorization checks.

## Contract

- Each preparation uses only the response to its own latest period request.
  An earlier preparation superseded by a period change or another preparation
  cannot open a modal, overwrite the current summary, or start a stale read
  after a delayed authentication wait.
- The modal shows the exact scope returned by the selected preview. Its copy
  and download controls are valid only while that generation remains current.
  Changing the period immediately disables both controls and announces the
  stale scope in the modal status (without waiting for a click). The old request
  remains visible for inspection but is explicitly stale. Preparing again
  creates a new enabled dialog with the newly selected scope.
- A stale copy click does not write to the clipboard. A stale download click
  does not request an export ticket. If the period changes while a ticket
  response is pending, no download is started; the user is told to prepare
  again. A clipboard write already in progress cannot be revoked, so the
  status explains if its request refers to the previous period.
- A current, explicitly prepared request still supports the existing
  redacted CSV ZIP and optional detailed chronological evidence. Export
  tickets remain subject to the existing local backend permission checks.
- No AI, business-system write, automatic upload, source authorization,
  recorder setting, retention rule, or REST/MCP contract changes.

## Reproducible synthetic regression

`node --test tests/js/analysis_handoff_scope.test.mjs` uses controlled
deferred API responses and a minimal DOM model to verify: a superseded modal
never opens, stale controls disable and announce their status immediately,
current copy/download
retains the exact selected scope, a late export ticket cannot trigger a
download, and an old authentication wait cannot start a stale preview read.

These are deterministic JavaScript contract checks, **not** an actual
consenting-user UX study, browser accessibility certification, provider
acceptance, or evidence that a customer outcome occurred. Existing browser,
native, packaging, accessibility and scale CI remain required before merge.
