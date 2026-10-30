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
- The authenticated preview must echo the requested scope and, for a date
  range, the same UTC start/end instants. The server serializes UTC bounds as
  `+00:00` while the browser sends `Z`; these are compared as instants, not
  byte strings. A mismatched/missing window fails closed before a modal or
  export. This guards response mix-ups; it does **not** authenticate an
  untrusted server or prove that the returned event counts are complete.
- Custom date inputs must be real calendar days. JavaScript's automatic
  normalization of impossible dates (for example February 30) must not
  silently change the evidence period.
- The modal shows the exact scope returned by the selected preview. Its copy
  and download controls are valid only while that generation remains current.
  Changing the period immediately disables both controls and announces the
  stale scope in the modal status (without waiting for a click). The old request
  remains visible for inspection but is explicitly stale. Preparing again
  creates a new enabled dialog with the newly selected scope.
- Before clicking an issued export link, the dashboard checks that the ticket
  response names the exact local `/v1/export/csvzip` endpoint, carries one
  nonempty matching ticket token, preserves the selected scope and UTC-equivalent
  range bounds, and keeps the user-requested detailed-evidence and name-redaction
  flags. External/protocol-relative URLs, fragments, duplicate/unknown query
  parameters, missing or contradictory ticket tokens, and changed privacy flags
  are refused. A refusal creates no download and leaves a deliberate retry
  available while the selection remains current. This is a client-side
  consistency guard, **not** a substitute for the backend's single-use,
  authenticated export-ticket binding and permission enforcement.
- A stale copy click does not write to the clipboard. A stale download click
  does not request an export ticket. If the period changes while a ticket
  response is pending, no download is started; the user is told to prepare
  again. A clipboard write already in progress cannot be revoked, so the
  status explains if its request refers to the previous period.
- A current, explicitly prepared request still supports the existing
  redacted CSV ZIP and optional detailed chronological evidence. Export
  tickets remain subject to the existing local backend permission checks.
- While an export ticket is pending, Download **and** the detailed-chronology
  checkbox are disabled. The requested detail level is captured before the
  asynchronous ticket call and announced in the pending and successful-download
  status; subsequent DOM changes cannot rewrite that already-issued request.
  Repeated clicks for the same handoff cannot create concurrent tickets. A
  ticket failure restores the controls for an explicit retry only if that period
  is still current; a period change leaves both controls disabled. A fresh
  preparation resets the detailed-chronology checkbox to unchecked. No
  background export or automatic retry occurs.
- Copy is a single-flight, user-initiated local clipboard operation. While
  the permission prompt/write is pending, Copy is disabled and its status
  announces local copying; repeated clicks cannot enqueue duplicate writes.
  Success or failure re-enables Copy only if the prepared period is still
  current, allowing an explicit retry after a permission failure. No AI
  invocation, upload or automatic retry occurs.
- Copy and Download share one status announcement and therefore cannot run
  simultaneously. While either operation is pending, both action buttons are
  disabled; the other action cannot replace its progress or completion message.
  Completion or failure restores both buttons only for the still-current
  prepared scope (and only if recorded evidence permits Download). A stale
  handoff remains disabled. This does not cancel a clipboard write already
  underway or grant any export permission.
- A delayed clipboard failure after the selected period changes retains the
  stale-period warning rather than replacing it with a generic clipboard
  message. A successful in-flight clipboard write cannot be revoked: the
  status discloses that it copied the **previous** period and the old Copy
  button remains disabled.
- No AI, business-system write, automatic upload, source authorization,
  recorder setting, retention rule, or REST/MCP contract changes.

## Reproducible synthetic regression

`node --test tests/js/analysis_handoff_scope.test.mjs` uses controlled
deferred API responses and a minimal DOM model to verify: a superseded modal
never opens, stale controls disable and announce their status immediately,
current copy/download retains the exact selected scope, a late export ticket cannot trigger a
download, an old authentication wait cannot start a stale preview read, a forged preview window cannot open a handoff, UTC-equivalent server bounds remain accepted,\nimpossible custom dates are rejected, repeated Download clicks issue one
export ticket, the detailed-evidence option is frozen and accurately reported during a pending ticket (including programmatic mutation), stale tickets cannot re-enable it, ticket failures allow a deliberate retry, forged or mismatched
export links cannot change the scope/redaction/detail settings or leave the
local endpoint, UTC-equivalent range bounds still pass, and delayed clipboard
errors preserve the stale-period warning. Deferred clipboard checks also cover
single-flight Copy, mutual exclusion with pending Download and the shared
status announcement, a permission-failure retry and successful completion after
a period change without re-enabling the old control.

These are deterministic JavaScript contract checks, **not** an actual
consenting-user UX study, browser accessibility certification, provider
acceptance, or evidence that a customer outcome occurred. Existing browser,
native, packaging, accessibility and scale CI remain required before merge.
