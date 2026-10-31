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


## Malformed preview fail-closed contract

The dashboard checks the preview shape before displaying event counts or
enabling handoff actions. It requires a nonblank request, safe nonnegative
integer canonical/retained counts, object maps with nonnegative integer
per-event/per-source counts, and an array of redacted example objects.
Optional date-chunk planning may be absent, but a supplied chunk list must
be an array of objects. Invalid responses show a local preparation error
instead of opening a broken modal, copying an undefined request or treating
a truthy string `"0"` as evidence available for export. This is defensive
client-side consistency, **not** server authorization or proof that event
counts are complete. A legitimate zero-event preview still permits copying
its explanatory request but does not offer a package download.

Synthetic regression cases cover malformed counts/maps, missing
request/examples, invalid chunk shapes and empty-evidence compatibility.

## Observed time and date-chunk integrity

A nonzero recorded-event preview requires valid ISO-8601 first/last observed
instants in chronological order. Zero-event previews may omit both; supplied
pairs must still be valid and ordered. Impossible calendar dates and locale
strings fail closed instead of displaying `Invalid Date` or reversed ranges.
UTC-equivalent offsets remain supported.

Optional date-chunk planning may be absent. Supplied chunks must have valid
ISO date or timestamp labels (`since`, `until`) and safe nonnegative integer
`event_rows`; the optional `more_dates_not_listed` indicator must be a
boolean. Invalid chunk coverage cannot render as `undefined` or enable
Copy/Download. These are presentation-shape checks, not completeness,
source-verification, live customer acceptance or business outcome claims.
Synthetic regressions cover malformed/absent/reversed times, impossible
dates, malformed chunk counts and valid offset/zero-evidence compatibility.

## Chronological date-chunk consistency

The optional per-day analysis plan is displayed as a sequence of evidence
windows, not independent unordered labels. Each supplied chunk must have
`since < until` as an actual instant, and every next chunk must begin at
or after the previous chunk ends. Reversed, zero-length, overlapping,
repeated and out-of-order chunks fail closed before the handoff modal,
clipboard or export-ticket request. Date-only labels are interpreted as
UTC midnight; equivalent offset timestamps compare by instant. Adjacent
chunks, gaps, an empty plan and an absent optional plan remain valid.
This protects the **presentation of coverage**, not the completeness or
truth of the underlying recorded events.

Synthetic modal regressions cover six invalid chronology patterns,
including a duplicate expressed with equivalent timezone offsets, and
a positive contiguous mixed date-only/UTC-offset sequence. No real-user
or provider validation is implied.

## Preview count conservation

The authenticated local preview's event-type and source count maps are
two partitions of the same selected canonical event rows. The handoff now
requires each partition to sum **exactly** to `canonical_rows`, with
`canonical_rows <= retained_rows` (the latter is the count of all retained
events, not just the selected period). Summation refuses integer overflow even
if every individual count is a valid safe integer. Otherwise the panel
refuses to open and cannot copy or request an export ticket.

The optional date plan lists at most the first 31 observed dates and may
omit dates, so its listed `event_rows` total need **not** equal the selected
count. It must not exceed the selected count or overflow. An absent/empty
plan, valid partial plan and zero-event copy-only handoff remain supported.
These checks catch inconsistent preview envelopes; they do **not** establish
complete capture, exact source truth, permission to export, real-world
workflow confirmation or business-outcome improvement.

Synthetic modal regressions cover source/event-type undercounts and
overcounts, retained-count disagreement, safe-integer sum overflow,
excess planned rows, zero-selected/positive-plan contradiction and a valid
partial-date plan. No real customer or provider acceptance is claimed.

## Work and capture-health count integrity

When the optional `work_evidence` block is present, its ordinary and
capture-health row counts must either both be absent (legacy metadata) or
both be safe nonnegative integers. Their sum must equal the selected
`canonical_rows`, and the health count must equal the sum of the
`capture_gap` and `capture_health` event-type partitions. The sums
are overflow-safe. A supplied recorder-liveness flag must be a boolean;
a supplied last-work timestamp must be a valid instant between the
preview's first and last observed instants. An absent work-evidence block
or legacy empty block remains valid, as do health-only and ordinary-only
histories.

This prevents a malformed preview from making an unsupported claim about
ordinary activity, recorder health or observed work coverage before the
user copies a request or starts an export. It does **not** establish that
the recorder was running, that unobserved intervals were productive or
unproductive, or that a business outcome occurred. The local backend
still owns authentication, export authorization and redaction. Synthetic
modal tests cover over/undercount, mismatched health partitions, overflow,
partial counts, invalid liveness and last-work bounds, plus valid mixed,
health-only and legacy responses.

## Selected-window containment of displayed date chunks

A preview can pass row-count conservation and chronological ordering yet list dates **outside** the selected evidence period. The handoff now requires each listed chunk's inclusive start and exclusive end to fall inside the exact server-echoed range, comparing timezone-aware instants (so equivalent UTC offsets are valid). For a `current` selection the optional recorder-run start is a lower bound; `all` is unbounded and must not smuggle in extra bounds. Malformed bounds, reversed range bounds and out-of-window chunks fail closed before Copy or Download. Valid gaps, truncated 31-day plans, absent plans, legacy optional metadata and zero-event handoffs remain supported.

The synthetic regression includes start/end overreach (also expressed using offset timestamps), UTC-equivalent exact boundaries and a forged bounded `all` scope. This prevents misleading **displayed plan coverage**, not proof of complete observation, verified source outcomes, customer consent or real-world competitor parity.

## Selected observed-range provenance

The first/last observed timestamps must describe events **inside** the selected
server-echoed evidence period, not another saved history. For `range`, the
first timestamp is at or after inclusive `since` and the last strictly
before exclusive `until`. For `current`, an optional recorder-run start
is an inclusive lower bound; `all` remains unbounded. Comparisons use
UTC-equivalent instants, not string equality. Zero-event previews with no
observed timestamps remain copy-only; legacy unbounded previews remain valid.

Malformed or mixed-up observed coverage refuses the modal, Copy and Download.
This is client-side evidence consistency, **not** proof of complete capture,
live consent, business outcomes or competitor parity. Synthetic tests cover
early/late range observations, exclusive-end rejection, timezone offsets,
valid inclusive-start/end-minus-one-second boundaries, current-run lower
bounds and all-history compatibility. Shared fixture observations now follow
the local calendar day rather than hardcoding October 2026.

## Date-chunk intersection with actual observed evidence

A selected week can contain a plausible, count-conserving, chronologically
ordered date chunk even when the preview's **first and last observed events**
are on a different day. The displayed plan would then claim positive evidence
for a time interval in which the preview itself says no event was observed.

Every date chunk with a positive `event_rows` count must now intersect the
inclusive span from `first_observed_at` to `last_observed_at`. Chunk ends
remain exclusive: a chunk ending exactly at the first observed instant cannot
contain that event. Timestamp comparisons use actual UTC instants, so
equivalent timezone offsets remain valid. A day-long chunk starting at midnight
may legitimately precede the first observed time on that same day; partial
and truncated plans remain supported. Zero-count optional legacy chunks are
not treated as evidence of observation and may fall outside that span, but
must still satisfy the selected-window and ordering checks.

Synthetic regressions cover a falsely claimed earlier day, a chunk ending
exactly at the first observation, a later same-day chunk, and offset-equivalent
valid intervals (including a chunk beginning at the last observed instant).
This is a **client-side contradiction check**, not a proof of source
completeness, business-case linkage, consent, provider truth, or a real pilot.

## Selected focus-example provenance

The local preview samples `redacted_examples` only from selected
`focus_span` rows, capped at six. Before displaying examples or allowing
Copy/Download, the dashboard requires no more examples than either six or
the selected focus-span count. When an example supplies `observed_at`,
it must be a valid offset-aware ISO instant within the first-to-last
selected observation span, inclusive. Missing optional timestamps remain
supported for legacy previews; zero-focus/health-only selections cannot
display stale focus context.

Synthetic modal tests reject zero-focus, count-overrun, over-cap, stale
before/after, malformed and numeric timestamp responses, while accepting
UTC-equivalent offsets, legacy omissions and health-only selections.
These checks detect contradictory preview envelopes, not complete source
coverage, guaranteed redaction, real consenting-user acceptance or parity.


## Empty-selection and health-only provenance

The authenticated preview's first/last observed timestamps come from selected
event rows. A selection with **zero** canonical rows must have **neither**
timestamp, even if retained history exists outside the selected period. A
zero-row preview with non-null first/last timestamps is inconsistent and cannot
open the Copy/Download handoff. Legitimate zero-row, null-bound previews still
allow the explanatory **Copy request** action without offering an export.

The optional `work_evidence.last_work_observed_at` represents an observed
ordinary work event, not a capture-health or gap event. When the selected
canonical rows consist entirely of `capture_gap` and `capture_health`
events, that timestamp must be absent. The guard derives the ordinary-event
count from the selected event-type partition even if older `work_evidence`
objects omit their explicit row counts. It **does not** require a work timestamp
whenever ordinary events exist: excluded-app records can have ordinary event
counts but no eligible last-work event.

Synthetic modal tests reject false observed timestamps for zero selected rows
with both empty and nonempty retained history, reject false last-work claims
for health-only selections with explicit or legacy-omitted work counts, and
accept health-only evidence with an unknown last-work timestamp as well as
ordinary/excluded-app evidence without a last-work timestamp. These checks
detect contradictory preview envelopes, not missing capture, completeness,
live recorder state, customer consent, provider verification, or business
outcomes. No source privileges, AI execution or business writes are added.


## Bounded displayed date-plan cardinality

The authenticated local preview selects at most the first **31** observed
date chunks (`days[:31]`) and separately reports
`more_dates_not_listed` when later dates exist. The handoff validator now
refuses an otherwise count-consistent preview containing **32 or more**
displayed chunks, rather than rendering an unbounded plan or silently
truncating server-returned evidence. It checks this before opening the
handoff modal, Copy or export-ticket Download.

Exactly 31 displayed chunks remain valid; when `more_dates_not_listed`
is explicitly true, all 31 must be present and the latest selected observation
must fall on a later unlisted date. An absent plan, fewer chunks and legacy
zero-count chunks retain compatibility only when no explicit coverage flag
contradicts their counts. Synthetic mock-DOM
regressions distinguish the exact 31-entry boundary from a forged 32-entry
response while preserving count conservation, chronological ordering,
observed-span intersection and the selected period. This is a bounded
client-side preview consistency check, **not** proof that every date was
captured, that sources are verified, or that a real consenting pilot
demonstrates competitor parity or commercial benefit.

## Honest completeness and omitted-date claims

The local preview explicitly sets `analysis_plan.complete_history_claimed=false`:
a selected time range is **not** proof of complete capture. A supplied
`true` completeness assertion (or a non-boolean value) is rejected before
the modal, Copy or Download. Older previews that omit the optional field
remain usable; a literal `false` remains a statement of uncertainty.

The backend's `more_dates_not_listed=true` means selected events exist on
dates beyond the **first 31 displayed UTC dates**. The dashboard requires
exactly 31 listed chunks, fewer listed rows than selected canonical rows,
and a latest observed event at or after the last listed chunk's exclusive
end. A forged omission flag for a short or fully accounted-for list is
inconsistent. Conversely, explicit `more_dates_not_listed=false` requires
the listed row counts to equal the selected canonical count; otherwise the
UI would falsely imply that an incomplete daily plan listed all dates.

An **absent** omission flag remains legacy-compatible, including short
partial plans, without treating it as a proof of completeness. Zero-count
legacy chunks are not positive evidence of observations. Synthetic modal
regressions cover forged true/false flags, missing rows, short truncation
claims, a latest observation still within the listed dates, and a valid
32-day selected history with 31 displayed days. These remain client-side
consistency checks, not guarantees of capture completeness or source truth. This is a
client-side contradiction check, not a proof of complete recording,
verified business workflows, consent, real provider state, a customer
pilot or competitor parity.

## Local AI lease visibility and rich-export privacy disclosure

The dashboard's authenticated preview reports a **snapshot** of local AI
saved-history access, not an authorization for the selected export or a promise
that access will remain active. When local AI access is enabled and the
`selected_range` lease has valid, ordered, timezone-aware bounds, the handoff
now displays both exact normalized UTC instants, the exclusive end, and any
future expiry. `all_saved`, `off`, and disabled local AI remain distinct.
Malformed, unrecognized, expired, or missing optional lease metadata is
displayed as **unverified** rather than as an affirmative permission. This
does not block a user's local copy or file download; the server still
authenticates the preview and separately authorizes single-use export tickets.
Preparing a file never widens local AI grants, and hosted AI only sees evidence
previously shared with it.

The detailed-chronology option is explicitly described as adding **raw local
event rows**. Name redaction is best effort, not anonymization: titles, URLs
and other metadata can remain sensitive in summary exports, and raw local
chronology can contain additional sensitive content. Users are told to inspect
the downloaded file before sharing. The detailed option remains unchecked by
default; no AI call, upload, new source privilege or business write occurs.

Deterministic mock-DOM regressions exercise valid selected-range bounds and
expiry, explicit off/all-saved/disabled states, malformed dates, reversed
bounds, expired grants, unknown modes, non-boolean enablement and missing
metadata. They also check that the rich-export warning is visible before a
detailed local ticket and that copying still works when lease metadata is
unverified. These are synthetic interface-contract tests, **not** real
consenting-user research, an IdP/provider validation, accessibility
certification, or evidence of competitor parity or business ROI.


## UTC daily-plan provenance and optional pagination metadata

The authenticated local preview builds each `analysis_plan.date_chunks` entry
from exactly **one UTC calendar date** in the selected SQLite event rows, then
clips its inclusive start/exclusive end to the selected window. A client that
accepted a two-day entry (or a short interval crossing UTC midnight) could
display a forged multi-day window as one daily analysis step even when its row
counts, selection bounds and observed first/last timestamps were otherwise
consistent. The handoff now requires every listed chunk to end no later than
the UTC midnight immediately following its start date. A clipped sub-day
interval, an offset representation of the same UTC instants, or a chunk ending
**exactly** at the next UTC midnight remains valid. Existing chronological,
31-entry, selected-window, observed-span and count checks still apply.

The optional plan metadata also cannot contradict the backend's shape: a
supplied `page_size` must be a positive safe integer, and a supplied
`chunks_respect_selected_window` must be the literal boolean `true`.
Older previews omitting either field remain accepted. This does not claim
that a positive page size proves every page was read, that an asserted flag
authenticates the source, or that a valid daily plan establishes complete
capture. It only refuses inconsistent metadata before rendering or allowing
Copy/Download.

The synthetic mock-DOM regression covers a forged two-day window, a
sub-hour midnight-crossing window, a clipped same-day offset window, an exact
UTC-midnight end, six invalid page-size shapes, false/string window-respect
flags, and positive current/legacy metadata. It does **not** replace native
Node, browser, accessibility, scale or packaging CI, or consenting-user
competitor-style acceptance.

## One displayed daily step per UTC date

The server emits at most one date-plan chunk per observed UTC calendar date.
Chronological, non-overlapping intervals alone do not enforce that invariant:
two adjacent sub-day fragments can both pass the older checks and appear as
two separate daily analysis steps for the same date. The handoff now compares
the **UTC calendar date of each chunk start** to the previous one and rejects
repeated dates, including offset-equivalent labels and zero-count fragments.
This does not prohibit a legitimate clipped partial day or an interval ending
exactly at the next UTC midnight. It is a displayed-plan consistency check,
not proof that the recorded events cover every part of that day.

Synthetic tests cover non-overlapping same-day splits, UTC-offset-equivalent
splits, and a zero-row second fragment. The 31/32-chunk boundary fixture now
uses **distinct UTC dates in an all-retained-history selection** instead of
31 one-second fragments of a single day. These are fake local responses, not
real-user usability research, live-source validation or verified outcomes.

## Exact explicit date-plan coverage reconciliation

For the authenticated backend's `analysis_plan`, the omission flag describes
whether its bounded SQL query found a **32nd distinct UTC date**. It is not a
generic indicator that a plan is partial. A selected 32-day history can
legitimately display the first 31 dates with `more_dates_not_listed=true`;
a one-day plan with one of two selected rows omitted cannot use that flag
to claim ordinary pagination.

When the flag is explicitly `false`, the safe-integer sum of displayed
`event_rows` must equal `canonical_rows`. When explicitly `true`, the
preview must contain 31 distinct ordered UTC date chunks, fewer displayed
rows than selected, and an observed last event on a later date. An omitted
flag is tolerated for legacy previews, but does not certify that the
displayed plan is exhaustive. Invalid or contradictory previews cannot open
the modal or issue Copy/Download; no server authorization is inferred.

Synthetic regressions distinguish legitimate 32-day truncation from a
forged one-day truncation, an undercounting explicit no-omission claim, an
empty explicit no-omission plan with selected events, and a last-observation
timestamp that contradicts an unlisted later date. This does not establish
complete recording, a live pilot, provider verification or competitor parity.


## Work-text exclusion disclosure integrity

The current local `/v1/analysis-preview` response includes
`work_text.included_in_activity_package: false`. The handoff can state that
visible work text is excluded **only when this explicit false is present**.
An explicit `true`, a string/number masquerading as false, a malformed
`work_text` block, or a non-boolean supplied `local_capture_enabled` flag
fails closed before opening the handoff or enabling Copy/Download. This avoids
presenting a known contradictory privacy disclosure as an assurance.

Older previews that omit `work_text`, omit its inclusion field or report
unknown/null inclusion remain compatible. Their modal instead warns that
exclusion is **not confirmed** and instructs the user to inspect the package
before sharing it; it does not assert that visible text, typed values or
clipboard contents are absent. The backend's actual export-ticket privacy and
redaction checks remain authoritative; this browser validation neither changes
the contents of a package nor grants permission to export it.

Deterministic mock-DOM tests cover explicit true, string/numeric inclusion,
malformed capture flags and container types, a verified false with successful
copy, and legacy missing/empty/null metadata with a visible uncertainty
warning. These synthetic tests do not prove real-package contents, live-provider
behavior, consenting-user acceptance or competitor parity.


## Rejecting ISO clock rollover in evidence and ticket provenance

JavaScript's built-in `Date.parse` accepts an apparent ISO instant such as
`2026-10-31T24:00:00Z` and normalizes it to midnight **on the following
date**. Checking only that the original `YYYY-MM-DD` is a valid calendar
date therefore did not protect the handoff from a time label that silently
changes days. In a date plan, that can misidentify the UTC day of a purported
observed step; in a saved-history lease, it can overstate a malformed grant;
and in a returned export-ticket URL, an invalid rollover bound can compare
equal to the requested instant.

The dashboard now uses one strict clock-shape check (hours 00–23, minutes
and seconds 00–59, timezone-aware ISO timestamps) **and shared real-calendar-day
validation** in addition to selected-window checks. This also prevents an
impossible ticket date such as `2026-02-30` from comparing equal to March 2. It rejects rollover instants in
preview observations and daily chunks, treats a malformed lease as
**unverified**, and refuses a ticket URL whose bounds only match because
`Date.parse` rolled its clock forward. Valid `00:00:00`, fractional
seconds and equivalent UTC-offset representations remain supported.
The authenticated backend's permission and single-use ticket checks still
apply independently; browser validation is not a source of authority.

Synthetic mock-DOM regressions cover malformed observed and daily-plan
instants, an expired-shape saved-history lease, and ticket bounds with rollover clocks or impossible calendar dates that
`Date.parse` considers identical to a valid selected local midnight. They do
not establish live-provider behavior, capture completeness, a real
consenting-user pilot, commercial outcomes or competitor parity.


## A later observed hour is not an omitted UTC date

The authenticated preview's `more_dates_not_listed=true` means the bounded
date query found a **32nd distinct UTC calendar date**. It cannot be justified
merely by an event timestamp later than the end of the 31st listed chunk.
For example, if that chunk was clipped to 10:00 UTC and the last observation
was at 12:00 UTC **on the same date**, comparing timestamps alone would
incorrectly advertise an unlisted date. This was especially misleading
when the displayed row totals already indicated some evidence was not listed.

The handoff now requires the last observation's **UTC day number** to be
strictly greater than the 31st chunk's UTC start-day number before trusting
an explicit omission flag. Timezone-offset representations are normalized
to UTC instants for this check, so a local date label from the previous day
does not hide a genuine 32nd UTC date. The existing 31-distinct-chunk,
safe-count, observed-span, chronological and selected-window checks remain.
Legacy previews without an explicit omission flag are still reviewable
without a completeness assurance.

A synthetic mock-DOM regression constructs 31 daily chunks with the last
clipped at 10:00, an omitted selected row, and a last observation at 12:00
on the same UTC date: Copy/Download must remain unavailable. The positive
case places that observation on the next UTC day using a negative timezone
offset and retains safe local Copy. These fixtures are not evidence of
real-user acceptance, complete capture, live-provider validation or ROI.


## First and last observed dates in explicit daily plans

The authenticated preview's daily-plan query groups selected events by UTC
calendar day and orders those days chronologically. Therefore an explicit
`more_dates_not_listed=true` plan must begin with a chunk that actually
contains `first_observed_at`, not merely list 31 later dates and show an
unlisted last observation. Likewise, when the omission flag is explicitly
`false`, the final listed chunk must contain `last_observed_at`. Both
endpoint checks compare absolute instants (including UTC-offset equivalents)
and treat the chunk's end as **exclusive**.

A conserved sum of displayed event counts cannot by itself establish that
the displayed dates cover the selected evidence span. The browser refuses
an explicitly contradictory plan before offering Copy or Download, while
retaining the existing safe-count, distinct-UTC-date, selected-window and
observed-span checks. For backward compatibility, older previews without
an explicit omission flag remain reviewable but do not certify complete
daily coverage. Zero-event plans remain valid without invented endpoints.

Five deterministic mock-DOM cases cover a 31-day truncated plan that omits
its earliest selected date, its valid first-date counterpart, an explicitly
complete two-day plan whose last observation falls outside its final chunk,
its valid counterpart, and the legacy omitted-flag case. These tests exercise
synthetic responses only: they do not prove capture completeness, real
consenting-user usability, live provider state, competitor parity or ROI.


## Explicit daily-plan dates require observed rows

The local preview's daily plan is constructed from UTC dates that have at least
one selected recorded event. A server response with an explicit
`more_dates_not_listed` boolean cannot therefore claim an empty
(`event_rows: 0`) daily chunk, even if the other chunks' counts add up to
`canonical_rows` and the first/last observed timestamps fall inside the
displayed range. The handoff now refuses such contradictory coverage
envelopes before opening Copy or Download. This also rejects fabricated
zero-count dates in an explicitly complete zero-event plan.

Legacy previews **without** an omission flag retain support for optional
zero-row date chunks; legitimate zero-event previews with an empty explicit
date plan remain copy-only. Synthetic mock-DOM regression cases exercise
complete, truncated, empty and legacy plans, including positive-count
compatibility. This is a client-side consistency check against the current
server contract, **not** independent proof of complete capture, source
verification, user consent, real-world pilot success or competitor parity.


## Scope-specific wording and unknown daily coverage

The review modal must not call a `current` recorder-run selection "all
available history" when the server omits the optional run-start timestamp.
It now distinguishes three **server-echoed** scopes without inferring extra
coverage: an explicitly bounded `range` (exclusive UTC end), a `current`
recorder run (with the returned start or "start not supplied"), and `all`
retained history (unbounded date selection). The displayed wording is escaped
before insertion into modal HTML. It does not broaden the request, change
source permissions, or establish that recording was continuous.

The daily breakdown is optional metadata, **not** an independent completeness
receipt. When `analysis_plan.date_chunks` is absent, the modal says no daily
breakdown was provided and coverage is unknown. An explicitly empty list says
no daily chunks were listed, not that there were zero events or complete
recording. Existing validated positive date chunks and the 31-date truncation
notice retain their behavior. The canonical selected-event count remains
visible independently of the optional plan.

Deterministic mock-DOM regressions exercise current-run scope with and without
a supplied start, unbounded all-history scope, an absent plan and an empty
plan while selected events exist. Each positive case retains user-initiated
Copy and makes no export-ticket request. These are synthetic UI consistency
checks, not proof of live source completeness, customer consent, competitor
parity or business outcomes. Native cross-platform/browser/accessibility/
scale/packaging CI is still required before merge.

## Latest-work timestamp: unknown is not none

The handoff's previous fallback rendered `Latest work event: none in this period`
when the optional `work_evidence.last_work_observed_at` was missing.
That is misleading for selected ordinary events from excluded apps, or for
older preview responses that omit work metadata: those events may exist even
though no latest-work timestamp was supplied.

The modal now distinguishes a supplied and validated latest-work timestamp,
a missing timestamp **despite selected ordinary events**, and a selection
containing **no recorded ordinary work events** (including health-only and
zero-event selections). For older previews, the ordinary count is derived
only from the validated canonical event count minus capture-health/gap
partitions. Missing metadata never becomes a claim that no work occurred;
absence of ordinary recorded events is not proof of inactivity. Capture
health/gap rows still do not establish that the recorder is running.

Five deterministic synthetic mock-DOM subtests cover a supplied timestamp,
missing timestamp with explicit ordinary counts, omitted legacy metadata,
health-only evidence, and zero selected events. They check visible wording,
copy-only behavior and unchanged download availability. No backend
permission, consent, export, source receipt or agent execution behavior
changes. These tests are not real consenting-user acceptance or evidence of
capture completeness, business outcomes, ROI or competitor parity. Native
Node/browser/accessibility/scale/packaging checks remain merge gates.

## Empty selected evidence is not verified inactivity

The period summary is also a provenance claim. When the authenticated preview
reports zero selected canonical events, it must not say "No activity in this
selection": an observation gap, consent choice, recorder outage or incomplete
retention can all produce an empty selection despite actual work. If the
backend reports other retained rows, the summary says only that this selected
period has no recorded events and that retained evidence exists. If it reports
zero retained rows, the summary says only that no retained events were
reported. **Neither branch infers inactivity, a disabled/enabled recorder,
complete capture or consent state.** The existing validated positive-event
summary is unchanged.

Two deterministic mock-DOM subtests cover zero selected events with and
without other retained evidence. They verify both initial refresh and
user-initiated Prepare for AI, copy-only review, disabled Download and no
export-ticket request. These are synthetic UI contract tests, not proof of
complete capture, real consenting-user acceptance or competitor parity.
Native Node/browser/accessibility/scale/packaging checks remain required.

## Copyable request when ordinary work evidence is absent

The server's generic request asks an AI to analyze the selected chronology,
but a local preview with zero ordinary work events cannot support an observed
workflow. The dashboard therefore appends an **evidence limitation** to the
copyable request (and the visible, copyable request preview) in exactly two
cases:

- **Zero selected events:** the preview has no recorded events for this
  selection, regardless of whether other retained events exist. This is not
  proof of inactivity, a running/stopped recorder, consent, or complete capture.
- **Capture-health/gap-only selection:** selected rows exist but are only
  capture-health or gap records. These are not ordinary workflow steps and
  cannot independently substantiate a business process.

The appended warning asks the receiving AI not to infer a workflow, business
completion, time savings, or ROI from absent ordinary evidence. Other
**currently authorized** sources may still be examined separately, with their
own provenance and limitations; otherwise the correct result is insufficient
evidence. This warning is a presentation/communication safeguard, **not**
permission enforcement, evidence of source completeness, or an AI execution
gate. The backend's permission and export-ticket checks remain authoritative.

When at least one ordinary work event is recorded, the dashboard copies the
original server-provided request byte-for-byte, without this additional
zero-evidence warning. The existing local modal remains user-initiated and
non-executing, and zero selected events still disable package download.

The deterministic synthetic mock-DOM test exercises zero retained events,
an empty selected period with retained history, capture-health/gap-only
observations, and an ordinary-work positive control. It checks the exact
copyable request, no automatic ticket/download, and that no false inference
or business-completion claim is introduced. It is **not** a real consenting
user study, external provider validation, or competitor parity evidence.


## Relative calendar rollover without a period-control change

The `Today` and `Last 7 days` selections are local calendar ranges. Their
UTC start/end instants change when local midnight passes, even if nobody
changes a dropdown or date input. The UI generation counter alone therefore
cannot establish that a pending preview, an already opened handoff or an
export ticket still belongs to the currently selected relative period.

The handoff now recomputes the local selection and requires the exact same
range instants before accepting a preview, opening the modal, copying a
request or starting a ticket download. If the date changes while a preview
is pending, the preview is refused with a retry instruction; no modal opens.
If the date changes after preparation, the first Copy/Download attempt
refuses the old scope, disables the stale controls and announces that a fresh
preparation is needed. If it changes during an export-ticket request, the
returned ticket cannot start a download. An already-started clipboard write
cannot be revoked; the existing completion message discloses the old period.

There is **no background midnight timer or automatic export**. Without a
user action, an already open modal may remain visually present until the
next refresh or action; the action guard still fails closed. Fixed custom
ranges and all-retained-history selections do not become stale solely
because the calendar day changes. Current-run scope has no client-side
source-run identity, so any run change and source permission/revocation
remain the server's responsibility. This is a local scope-consistency guard,
not proof of capture completeness or authorization.

Synthetic tests inject a controllable local clock into the actual dashboard
script and exercise rollover during preview, after modal opening and while a
single-use ticket is pending, plus fixed-range/all-history positive controls.
They do not establish real-user usability, browser accessibility, customer
consent, provider acceptance, commercial benefit or competitor parity.


## Zero-event copy-only affordance

A preview with **zero selected recorded events** can still provide an
explanatory request for the user to copy, but cannot supply an event-evidence
ZIP. The modal now states that limitation visibly, does not promise a full-period
file in its large-history guidance, and disables both Download and the
detailed-chronology checkbox. The disabled checkbox cannot imply that a
detailed package is configurable when no package exists. Programmatically
invoking Download still issues no ticket. This is true both when other retained
history exists and when the backend reports zero retained rows.

Selected **capture-health/gap-only rows are not zero recorded rows**. They may
still be exported for user-reviewed diagnostics with the same existing local
permission/redaction checks, while the copyable request warns that they are
not evidence of an ordinary workflow. Ordinary-work positive previews retain
their existing detailed-export option. No change is made to server-side export
eligibility or to the meaning of capture gaps.

The two deterministic synthetic Node regressions in
`tests/js/analysis_handoff_scope.test.mjs` exercise both empty-selection
variants and ordinary/health-only positive controls, including programmatic
disabled-button invocation and an explicit detailed export ticket. They do
not verify a live browser, accessibility audit, current consent, capture
completeness, inactivity, real provider acceptance or commercial benefit.
