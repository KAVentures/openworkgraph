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

The deterministic synthetic Node regressions in
`tests/js/analysis_handoff_scope.test.mjs` exercise both empty-selection
variants and ordinary/health-only positive controls, including programmatic
disabled-button invocation and an explicit detailed export ticket. They do
not verify a live browser, accessibility audit, current consent, capture
completeness, inactivity, real provider acceptance or commercial benefit.

## Server UTC serialization and local scope freshness

The authenticated backend serializes UTC preview bounds with `+00:00`, while
browser period selections use `Z`. A bytewise comparison of those strings
incorrectly treated **every unchanged range** as stale after its preview
returned, preventing the user from opening a legitimate range handoff. The
scope-freshness check now validates and compares the actual UTC instants.
An equivalent `+02:00` representation also remains valid; a shifted instant
or a local-midnight rollover still fails closed. This does not make a stale
server response authoritative or override the export-ticket's independent
scope/privacy checks. Synthetic regressions cover equivalent offsets with
copy and detailed download, plus a changed-instant refusal. Native browser,
accessibility, packaged and cross-platform CI remain required.

## Redacted sample-label provenance and safe rendering

The six-at-most focus-span context examples are *selected, redacted
observations*, not arbitrary JSON labels. Before the handoff opens, the
browser refuses an example whose `app` or `window_title` is an object,
array or number, or whose supplied `app` is blank/whitespace-only. This
prevents misleading `[object Object]` or numeric pseudo-app names from
being presented as observed evidence. A missing/null app is displayed
explicitly as **Unknown app**; a missing/null title remains **Untitled
activity**. Ordinary text labels, optional legacy timestamps and safe
HTML-escaped labels continue to work. These labels are not an identity
attestation, proof of workflow completeness, or authorization to act.

The deterministic mock-DOM tests cover eight malformed app/title cases,
three valid/missing/HTML-looking label cases, no modal or export on
malformed evidence, safe escaping, and an unchanged user-initiated
copy-only request for valid previews. Native browser/accessibility and
full CI are still required; these synthetic cases do not substitute for
consenting-user pilot evidence.
## Bounded reported-source breakdown in the review modal

The local preview groups selected events by the **reported source label**. These
labels are not verified provider identities, business-object attestations or
independent proof of complete capture. A selected import or retained history
can have many distinct labels; concatenating all of them into the review modal
can obscure the consent and evidence limitations, or make the modal expensive
to render.

The modal shows **at most eight** source categories, ranked by descending
validated selected-event count (literal source-label tie break), not by the
backend's arbitrary map insertion order. A label longer than **96 Unicode code points** is visibly
shortened with a **[source label truncated]** marker, rather than being
silently presented as the full identity. Blank labels are explicitly shown as
**[blank source label]**. Labels remain HTML-escaped. If categories are
omitted, the modal states both their **exact category count** and the **sum
of selected events** they represent; the existing preview validation still
requires the full source partition to sum to the selected canonical event
count. The bounded display does not discard or rewrite the source map in the
backend, request, or existing user-initiated export.

Deterministic synthetic mock-DOM regressions cover 12 distinct source
categories with four omitted events, long HTML-looking labels, Unicode
codepoint truncation, blank labels, and ordinary single-source compatibility.
The displayed subset is not a source-completeness claim; it is a deliberately
bounded review aid. Native browser, accessibility, cross-platform and
packaging checks remain required, as do real consenting-user acceptance and
provider/source verification before claiming competitor parity.

## Visually ambiguous evidence labels in the review modal

Imported source category names and redacted focus app/window-title labels are
**untrusted display text**, not provider-authenticated identities. HTML escaping
alone does not reveal Unicode bidirectional overrides, isolates, directional
marks, or embedded control characters that could reorder or conceal the
surrounding evidence count or title. The review now prints those code points as
visible `[U+XXXX]` markers (C0/C1/DEL, U+061C, U+200E/U+200F,
U+202A–U+202E, and U+2066–U+2069). This is a *display-only* transformation:
source keys, selected counts, event evidence, exports, consent and copyable
server request text remain unchanged.

The source preview still displays at most eight categories, with 96 Unicode
code points per visible label and an explicit truncation marker. Redacted focus
app/title examples now use the same 96-codepoint display bound and identify
truncation as a **sample label** limit. The UI does not claim that a shortened
or control-marked label is the complete original, nor that it is a verified
provider identity. Ordinary right-to-left script, emoji and HTML-like text
remain visible (HTML-like text stays escaped). This does not establish
complete Unicode confusable detection or sanitization of downloaded files;
users must inspect files before sharing them.

Synthetic mock-DOM regression cases cover a bidi override, directional
isolate, embedded newline, C1 control, bounded focus example, ordinary Arabic
text and HTML-like text. They do not replace native Node, Chromium,
accessibility, cross-platform, scale or real consenting-user acceptance.


### 2026-10-31 — source-count delimiter and invisible-label disclosure (Worker B)

The bounded reported-source preview is **not a verified provider identity list**. A
single imported source name may contain punctuation that looks like a separate
`source: count` entry or the `; omitted` disclosure. In the modal **only**,
commas, colons and semicolons inside source names now appear as explicit
`[U+002C]`, `[U+003A]` and `[U+003B]` markers; the actual list/count
separators and exact omitted totals remain distinct. Selected default-ignorable
formatting characters (soft hyphen, combining grapheme joiner, Mongolian vowel
separator, zero-width space/non-joiner/joiner, word joiner and BOM) are also
shown as `[U+XXXX]` in source and focus-example labels. Ordinary names such
as `desktop` and Arabic text without such characters render as before.

This is a **display-only ambiguity safeguard**, not a source-identity
verification, a change to canonical labels, a provider authorization, or a
complete Unicode-confusables defense. Existing source counts, event partition,
selection, copyable request, consent, export and download behavior remain
unchanged. Synthetic mock-DOM regressions cover a forged source-count/
omission label, zero-width/default-ignorable names, unchanged ordinary labels
and no unintended ticket/export on Copy. They do not replace native Node,
Chromium, WCAG, cross-platform, scale or packaging checks, or a consenting
real-user pilot. Claim: [#326 comment 6546499009](https://github.com/KAVentures/openworkgraph/issues/326#issuecomment-6546499009).


### 2026-10-31 — redacted sample omission is not zero focus evidence (Worker B)

The Prepare for AI modal formerly displayed **"No focus spans in this
selection"** whenever the `redacted_examples` array was empty, even if the
selected preview counted positive `event_types.focus_span`. The optional,
bounded sample list may be empty because examples were not supplied; it does
not negate the independently counted focus-span events. The review now
distinguishes:

- **Positive focus count, no samples:** disclose the exact selected
  focus-span count and explicitly state that no redacted examples were
  supplied; absence of examples does not establish absence of observed focus.
- **Positive count, one or more samples:** show both the number of supplied
  redacted examples and the selected focus-span event count; do not present
  samples as proof of complete context capture.
- **Zero focus count:** state only that no focus-span events were recorded
  *in the selection*, not that the person was inactive or that capture was
  complete.

The displayed examples remain bounded, text-escaped, and explicitly
best-effort redacted. This is a **display-only** evidence-omission correction:
no changes to selected event counts, source identity, privacy/consent,
retention, export, request text, agent execution or provider reads.

Four synthetic mock-DOM regression subcases cover omitted, partial, full-
count and genuinely zero focus examples, including copy-only behavior,
no tickets/downloads, and escaped sample content. They do not replace native
Node/Chromium/WCAG/scale/packaging CI, or realistic consenting-user review.
Claim: [#326 comment 6547182775](https://github.com/KAVentures/openworkgraph/issues/326#issuecomment-6547182775).


### 2026-10-31 — Unicode line/paragraph separators and invisible format controls (Worker B)

The local evidence review is a **display of untrusted source labels and
redacted focus examples**, not a provider identity attestation. HTML escaping
and the existing C0/C1/bidi protections do not visibly distinguish the
Unicode LINE SEPARATOR (U+2028) and PARAGRAPH SEPARATOR (U+2029): one imported
source name could appear as two lines of source counts. Invisible mathematical
formatting characters U+2061–U+2064 and deprecated directional controls
U+206A–U+206F could also conceal otherwise distinct source/sample names.

The modal now displays these code points as explicit `[U+XXXX]` markers in
both source and redacted sample labels. It continues to show at most eight
reported source categories, bound each displayed label to 96 code points,
HTML-escape the rendered result, and disclose omitted categories/events.
**Canonical source keys, evidence counts, copyable AI request text, local
export bytes, consent and permissions are unchanged.** This is not a
complete Unicode confusable defense, verified source identity, evidence
completeness proof, or authorization to run an agent.

Deterministic synthetic Node/mock-DOM regressions exercise source labels
containing line and paragraph separators, mathematical/deprecated invisible
controls, ordinary labels, redacted focus examples, the exact selected
focus-event count, and copy-only behavior without ticket/download. Native
Node, Chromium, accessibility, cross-platform, scale and packaging CI
remain independent acceptance gates; no real consenting-user pilot or
commercial benefit is claimed. Coordination: [#326 claim 6547775463](https://github.com/KAVentures/openworkgraph/issues/326#issuecomment-6547775463).


### 2026-10-31 — local preview is not an AI transfer or immutable export (Worker B)

The Prepare for AI modal previously headed its selected-event metadata
**"What the AI will receive"**. That was misleading for its two distinct,
explicit actions:

- **Copy request:** copies only the prepared request text to the local
  clipboard. It does not attach an evidence package, call a model, or send
  the selected event rows to an AI. The completion status now says this.
- **Download evidence package:** requests a local export ticket and starts
  a local download only on explicit click. A user must inspect and separately
  attach/share that file with any AI. The modal labels the metadata as a
  **local preview, not sent to an AI**.

Scope freshness is a separate limitation. **Current recorder run** can
change as events arrive or the recorder restarts; **all retained history**
can change with new/deleted/inaccessible evidence. Even a **fixed date
range** does not freeze the underlying rows, consent, retention or access.
The selected preview is therefore **not an immutable export snapshot**,
nor a checksum of a later download or proof of complete capture. The modal
now explicitly tells users to review the actual downloaded file before
sharing. Existing ticket-bound scope/privacy validation and stale relative
date rejection remain intact; this change does **not** add snapshot isolation
or new read/export permissions.

Three deterministic synthetic mock-DOM subtests cover the range, current
run and all-history disclosures; they verify the prompt-only copy path,
zero export tickets/downloads on Copy, and no implicit AI transfer. These
tests do not establish native Node/Chromium/WCAG/scale/packaging acceptance
or a real consenting-user pilot. Coordination: [#326 claim 6548395286](https://github.com/KAVentures/openworkgraph/issues/326#issuecomment-6548395286).


## Redacted focus-example observation-time provenance

The optional redacted focus examples are bounded samples, not a complete
history. Each displayed sample now distinguishes a supplied, validated
`observed_at` timestamp from a legacy sample without one. A timestamped
sample is labeled **observed at** and rendered as a semantic `<time>` element
with its original ISO-8601 offset-aware value. It is not silently converted
to the viewer's local timezone. An absent or explicit-null timestamp is
labeled **observation time not supplied**, never inferred from the overall
first/last observation span or from neighboring examples.

The existing fail-closed validator still rejects malformed or out-of-span
sample timestamps and over-counted samples before opening the review. The
display-only change does not alter canonical observations, request text,
consent, export contents, ticket permissions, or agent execution. The
synthetic Node mock-DOM regression covers UTC, offset-equivalent, absent and
null sample timestamps, confirms no ticket/download on Copy, and preserves
the request-only clipboard contract. It is not a real-browser accessibility
audit, a live-provider validation, or a consenting-customer pilot.


## Exact fractional-second scope identity (Worker B, 2026-10-31)

The browser previously compared the requested period with the preview's echoed
bounds and export-ticket URL using `Date.parse` equality. JavaScript
`Date.parse` keeps only millisecond precision: a returned
`2026-10-31T00:00:00.000001Z` bound can compare equal to the requested
`2026-10-31T00:00:00.000Z`. That is not an exact period match, and a
ticket response must not silently widen or narrow the user's chosen evidence.

The local handoff now compares **the exact UTC whole second and normalized
decimal fractional second**, after strict calendar/clock validation. This
accepts equivalent representations (`Z` versus `+00:00`, offset-aware
representations of the same instant, `.000` versus `.000000`) while
rejecting a one-microsecond difference. The comparison applies to preview
scope echoes, stale-selection checks and export-ticket URL bounds. It does
not relax server-side authenticated ticket authorization, change evidence
rows or export content, or add any provider, AI, upload or write capability.

Deterministic synthetic mock-DOM regressions cover one-microsecond changes
to either preview boundary, a shifted ticket bound that must not download,
and an exact six-digit fractional/UTC-offset ticket that remains downloadable
after explicit click. These tests are not a live provider acceptance, a
real consenting-user pilot, a complete audit of every timestamp in the
package, or a substitute for exact-head Node/Chromium/accessibility/scale/
packaging CI. Coordination: [#326 claim 6549753527](https://github.com/KAVentures/openworkgraph/issues/326#issuecomment-6549753527).


## Exact observed chronology and redacted sample containment (Worker B, 2026-11-01)

The selected preview's **first and last observed timestamps**, optional
**redacted focus-example observation times**, and optional **last ordinary
work timestamp** must agree at their full supplied fractional-second
precision. A one-microsecond reversal or an example just outside the observed
span must not be accepted because `Date.parse` rounds both values into the
same millisecond. The same rule applies to the inclusive start of a current
recorder-run window and to strictly ordered selected-range bounds.

The existing exact-offset-aware comparison is reused: UTC whole seconds
are compared first, then all decimal fractional digits, treating trailing
zeros as insignificant. Malformed timestamps fail closed. This prevents a
preview from presenting unsupported temporal provenance or enabling
Copy/Download after inconsistent evidence was returned. It does **not**
verify the completeness of captured work, freeze a snapshot, authenticate
the underlying observation source, or grant an export or AI permission.

Synthetic mock-DOM tests exercise reversed microsecond observations,
examples just before/after their observed span, a latest-work timestamp
outside that span, current-run starts one microsecond before/after the first
observation, and a valid microsecond-ordered sample. The negative cases
assert no handoff modal, clipboard copy, ticket or download; the positive
case remains copy-only. These tests do not replace exact-head native
Node/Chromium/accessibility/scale/packaging checks or consenting-user pilots.
Coordination: [#326 claim 6550343337](https://github.com/KAVentures/openworkgraph/issues/326#issuecomment-6550343337).


## Exact UTC daily-plan boundaries (Worker B, 2026-11-01)

The optional `analysis_plan.date_chunks` list describes selected-event counts
by UTC date. A positive-count chunk is evidence metadata, not proof of a
complete activity history. Even after exact observation-span validation, the
reviewer previously compared chunk start/end, selected-period clipping,
first/last event containment and next-midnight ceilings using JavaScript
`Date.parse`, which truncates fractional seconds to milliseconds.

This permitted a chunk to start **one microsecond after the first observed
event**, start **one microsecond after the last observed event**, extend
**one microsecond beyond the selected range**, or cross UTC midnight by one
microsecond while still appearing consistent with its declared coverage.
Conversely, a valid exact microsecond span could be rejected as empty because
its start and end both truncated to the same millisecond.

The review now uses the existing strict, offset-aware exact-instant comparator
for those daily-chunk relationships. Date-only chunk labels mean UTC midnight;
full ISO timestamps retain their original fractional precision and offset.
The 31-date limit, UTC-day uniqueness, count conservation, explicit
complete/truncated coverage flags and local-only Copy/Download rules remain
unchanged. A contradictory preview is withheld **before** either action,
rather than silently repairing or rewriting the underlying evidence.

Two deterministic synthetic mock-DOM test functions exercise six negative and
positive cases: a one-microsecond omitted first event, a positive chunk after
the last event, a post-midnight spill, an exact microsecond interval expressed
with an equivalent +02:00 offset, a chunk extending beyond the requested
period, and a correctly clipped UTC-equivalent end. These are **offline
synthetic** assertions, not native cross-platform/Chromium/WCAG/scale/
packaging acceptance or a consenting-user pilot. They do not verify
underlying source authenticity, provider completeness, snapshot immutability,
commercial ROI, or competitor parity.

Coordination: [#326 claim 6550924281](https://github.com/KAVentures/openworkgraph/issues/326#issuecomment-6550924281).

## Exact local AI saved-history lease disclosure (Worker B, 2026-11-01)

The Prepare for AI modal reports a **snapshot** of local AI saved-history
consent. It does not grant permission, authorize a file download, or imply
that the AI has received any evidence. Previously, that snapshot normalized
selected-range and expiry instants with `Date.parse` and
`Date.toISOString`, discarding fractions beyond milliseconds. A valid
selected-range grant lasting only one microsecond could appear unverified;
a displayed grant bound or expiry could differ from its supplied value.

The disclosure now normalizes valid offset-aware instants to UTC **without
losing fractional digits**. Whole-second or millisecond values retain the
existing `.000Z` display convention; higher precision remains visible.
The strict exact-instant comparator checks that selected-range start is
before its exclusive end and that an expiry is later than the local clock.
Malformed, reversed, unknown or expired grants remain **unverified**.
Because the local clock is millisecond-resolution, an expiry exactly one
microsecond after the current clock instant is still future-dated; the
display retains that microsecond rather than rounding it away.

Synthetic Node mock-DOM regressions cover a one-microsecond selected grant
with offset-equivalent endpoints, a one-microsecond future expiry, an
expiry equal to the local clock, and an invalid rollover clock. Copy
remains request-only, with no export ticket or evidence download. This
is only truthful local display of reported consent metadata: actual
permissions, revocation, export authorization and provider state are
unchanged and must be checked independently. Tests are **not** native
cross-platform/Chromium/WCAG/scale/packaging acceptance, a real consenting
pilot, or proof of competitor parity.

Coordination: [#326 claim 6551469704](https://github.com/KAVentures/openworkgraph/issues/326#issuecomment-6551469704).


## Exact observation provenance in the period summary (Worker B, 2026-11-01)

The compact period summary previously rendered the first and last observed
timestamps **only** with JavaScript `Date.toLocaleString()`. That formatting
is helpful for a quick local-time overview, but drops fractional seconds and
the source-reported timezone offset. Two events separated by one microsecond
can consequently appear to have the same observation time, even though the
preview's validation correctly distinguishes them.

The summary now retains its localized overview **and** displays the two
original validated ISO timestamps verbatim, explicitly labeled as reported
source times with their offsets preserved. The timestamps are already
validated as strict offset-aware instants before display; the change does not
claim their source identity is authenticated or that capture is complete.
It does not rewrite canonical events, normalize away source offsets, freeze
the preview, change export contents, or authorize an AI to read local data.

Deterministic synthetic Node mock-DOM regressions cover distinct events one
microsecond apart in the same displayed local second, an equivalent
`+02:00` source offset preserved verbatim, and a zero-event preview that
continues to state uncertainty rather than invent observation times. They
also assert that Copy remains request-only, without an export ticket,
download or model invocation. These are not native browser/WCAG, real
consenting-customer, provider-validation or competitor-parity results.

Coordination: [#326 claim 6552128864](https://github.com/KAVentures/openworkgraph/issues/326#issuecomment-6552128864).


## Explicit evidence sufficiency in the AI handoff review (Worker B, 2026-11-01)

The local preview is **not** a workflow-confirmation or business-outcome
verification engine. The modal now distinguishes three review states using only
validated selected event counts:

- **No recorded events:** insufficient for workflow review; capture coverage
  and consent status are unverified. No evidence download is offered, but the
  explanatory request remains copyable.
- **Capture-health/gap records only:** insufficient for workflow review. The
  existing explicit download remains available, but its button calls the file
  a **capture-health diagnostic package**, not an AI workflow evidence package.
  The data and export permissions are unchanged. The modal does not invite
  workflow discovery from diagnostic rows.
- **Ordinary work observations present:** observations can be reviewed, but no
  workflow, business completion, automation opportunity, or ROI is verified.
  Human review is required before confirmation or handoff decisions.

For insufficient evidence, the modal subtitle and introduction are neutral
limitations guidance rather than an invitation to discover opportunities or
infer workflows from diagnostics. When ordinary observations exist, the
introduction still requires explicit user sharing before AI review. The initial status also
identifies health-only downloads as diagnostics. This is a **display-only
classification**, not a new evidence source, model judgment, permission,
confirmation, execution or guarantee of completeness. It does not disable
explicit diagnostic exports or weaken backend export-ticket checks.

A deterministic synthetic mock-DOM test covers all three states, the modal
subtitle, evidence grade, download label and availability, and Copy's
request-only behavior without tickets or downloads. Real browser, accessibility,
consenting-user and live-provider acceptance remain separate gates.

Coordination: [#326 claim 6552672223](https://github.com/KAVentures/openworkgraph/issues/326#issuecomment-6552672223).

## Daily breakdown language follows evidence sufficiency

The expandable daily breakdown is **not** itself a verified workflow map. When
ordinary work observations exist, it may describe optional user-controlled
day-by-day AI review of the selected evidence, with the existing caveats about
coverage, redaction and incomplete history. No AI is invoked by opening it.

When the selected period contains **only capture-health/gap rows**, the same
bounded UTC date list remains visible as a *diagnostic* chronology, but the
heading and introduction must not instruct an AI to discover repeated work or
automation opportunities. The existing explicitly requested local download
remains available and is identified as a diagnostic package, not proof of work.

When **no rows** were recorded, the date section says there is nothing to
analyze and that missing observations do not establish inactivity, complete
capture or consent. It does not invite an AI to process days, and there is no
evidence-file download. Copying the explanatory request is still local and
does not trigger an export ticket, upload or model call.

Synthetic DOM regressions cover all three categories and a nonempty health-only
UTC daily chunk. These are disclosure checks, **not** validation of recorder
coverage, live consent, customer workflows or competitor parity.

## Dominant-source visibility in bounded review (Worker B, 2026-11-01)

The Prepare for AI review lists only eight reported source categories. Previously
it used the incoming source-map insertion order. An imported map with eight
zero-count or rare labels first could hide a source accounting for nearly all
selected observations, while still reporting only an undifferentiated omitted
total. That makes the bounded review less useful for deciding what evidence to
inspect before an explicit agent-neutral handoff.

The **display-only** list now orders the validated partition by **descending
selected-event count**, breaking ties by literal source-label order. This makes
the same source map reproducible across insertion orders and keeps high-count
sources visible. The summary still discloses the **exact number of omitted
categories and their total selected events**, bounds visible labels to 96 Unicode
code points, escapes untrusted HTML/format controls, and explicitly calls source
names *reported labels, not verified identities*. The underlying source map,
canonical observations, permissions, redaction, export ticket, copied request,
consent, retention and backend API are unchanged.

Three deterministic synthetic mock-DOM regressions cover a late dominant
source preceded by eight zero-count categories, a late high-volume source with
positive-count low-volume omissions, and reproducible literal-name ordering
when counts tie. They also assert Copy remains request-only and initiates no
export ticket or download. These checks are not real-browser/WCAG, real-provider,
consenting-user pilot, verified business outcome or competitor-parity evidence.

Coordination: [#326 claim 6553708598](https://github.com/KAVentures/openworkgraph/issues/326#issuecomment-6553708598).


## Colliding rendered source labels are not source identity (Worker B, 2026-11-01)

A bounded source review can conceal an important distinction even when every
selected-event count is valid. Separate **literal** source-map keys can become
the same *displayed* label: for example, two long names that share the first
96 Unicode code points, a source name with a bidi override versus a literal
`[U+202E]` sequence, or two different blank/whitespace-only keys. Without
an explicit distinction, a reviewer could mistake two reported categories for
one verified application/provider and overinterpret a workflow map.

The Prepare for AI source summary now adds
`[distinct reported category 1/2]`, `[distinct reported category 2/2]`
(and corresponding ordinals) **only when two or more of the eight visible
categories have the same rendered label**. The ordinals follow the existing
descending selected-event-count and literal-key tie ordering. They distinguish
displayed categories, **not authenticated provider identities**. Raw labels
are never copied into these ordinals, normalized, merged or rewritten.

The eight-category limit, safe display escaping, exact omitted category and
selected-event totals, ordinary/diagnostic evidence grading, and user-controlled
request-only Copy and local evidence download remain unchanged. If a colliding
category is outside the eight visible entries, the omitted-category disclosure
still applies; this preview does not promise to enumerate every literal label.
The downloaded file must be inspected separately before sharing, and a
reported source category alone cannot establish a true business workflow.

Three deterministic synthetic mock-DOM regressions cover shared truncated
prefixes, bidi-control/literal-codepoint collisions, and blank versus
whitespace-only labels. Each also checks Copy triggers no export ticket or
download. These are not actual Chromium/WCAG, real consenting-user pilot,
verified source-identity, business-outcome or competitor-parity results.

Coordination: [#326 claim 6554231894](https://github.com/KAVentures/openworkgraph/issues/326#issuecomment-6554231894).


## Missing focus labels are not observed source identities (Worker B, 2026-11-01)

The bounded redacted focus examples are **samples**, not an authenticated
application inventory or complete record of activity. Previously, a missing
`app` was rendered as `Unknown app`, and an absent, null, or empty
`window_title` as `Untitled activity`. Those phrases could themselves be
literal supplied values, making missing provenance indistinguishable from a
reported label. An explicitly blank title also looked like a named activity.

The review now separates **`app not supplied`** and
**`title not supplied`** from **`reported app: …`** and
**`reported title: …`**. Explicit empty and whitespace-only supplied titles
receive distinct visible placeholders. These are presentation-only provenance
markers; they do not verify the app/title or change the canonical event,
sample, redaction, source identities, export payload, or copyable AI request.
Untrusted supplied labels remain bounded and HTML/control-character escaped.
The observation time still displays the exact supplied offset-aware instant,
or explicitly says when the time was not supplied.

Deterministic synthetic mock-DOM regression cases distinguish absent/null
metadata from literal `Unknown app` / `Untitled activity`, explicit empty
and whitespace-only titles, and a literal `title not supplied` value.
Existing escaping and request-only Copy tests remain, and no export ticket,
download, upload, model call or business write is initiated by copying.
These tests are **not** real-browser/WCAG acceptance, a consenting-user pilot,
provider-identity verification, a business-outcome proof or competitor parity.

Coordination: [#326 claim 6554762405](https://github.com/KAVentures/openworkgraph/issues/326#issuecomment-6554762405).


## Selected versus retained evidence accounting

The local overview now shows both `canonical_rows` (observations selected
for this preview) and `retained_rows` (the backend's reported retained row
count). The Prepare for AI modal repeats these values for explicit human review.
The validated relationship is `0 <= canonical_rows <= retained_rows`; it is
not an estimate of recorder uptime, capture completeness, user productivity,
or the number of completed business cases.

A larger retained count does **not** mean that the difference represents missed
work. Rows may fall outside the selected time window or otherwise be excluded
from this preview. Conversely, equal counts do **not** prove that all work was
captured. These are descriptive counts from the current local response, not a
durable evidence snapshot or permission grant. Existing zero-event warnings,
copy-only behavior, export-ticket validation, redaction and consent rules are
unchanged.

Two synthetic mock-DOM regressions check selected < retained and selected =
retained, including no invented completeness claim and request-only Copy.
They do not establish real-user acceptance, recorder completeness, verified
business outcomes or competitor parity.

## Recorder-liveness evidence cannot be asserted by a preview

The local `server/analysis_handoff.py` preview reports
`work_evidence.recorder_liveness_verified: false`. Observed event counts,
capture-health rows and gaps do **not** establish that a recorder is currently
running or that all work was captured. A returned preview with an affirmative
`true` liveness flag contradicts this evidence contract and now fails
validation before opening the review modal or enabling Copy/Download. The
legacy absent/null flag and explicit `false` remain reviewable with the
existing unverified-recorder warning; this is not a new consent or recording
status check.

Synthetic mock-DOM regression cases cover affirmative liveness on both ordinary
work and capture-health-only selections, plus explicit false, absent and null
legacy metadata. They verify that rejected previews trigger no clipboard,
export-ticket or download operation. These checks do **not** prove live capture,
source identity, consenting-user acceptance, or competitor parity. The backend,
retained events, permission decisions, export contents and AI execution are
unchanged.


## Saved-history grant timestamps are disclosure evidence, not authorization

The Prepare for AI review previously treated a syntactically recognizable
`local_ai_history_access.mode` and a future `expires_at` as enough to display
an affirmative saved-history grant, even when a supplied `granted_at` was
malformed, in the future, or later than the expiration. This could mislead a
reviewer about when consent was granted. Production
`shared/history_policy.py:set_ai_history_access` records `granted_at` for
new saved-history grants, but this browser preview is only a local snapshot.

When `granted_at` is supplied, the review now requires an exact offset-aware
timestamp no later than the current local clock and, if an expiration exists,
strictly earlier than the expiration. Fractional seconds are compared without
millisecond truncation; one-microsecond ordering differences remain visible.
Malformed or contradictory timestamps leave the permission **unverified**;
they do not claim consent was denied, revoke a grant or alter any backend
permission decision. Existing expiry validation remains in place.

Legacy previews that omit `granted_at` (or report null) remain reviewable
under the pre-existing snapshot-only caveat. A valid past grant with no expiry
is also supported, consistent with the local indefinite-grant policy. The
dashboard does not infer that a copied request, evidence download or AI
handoff itself grants saved-history access. The user must check current
permissions independently; there is no AI execution or upload in this flow.

Eight deterministic synthetic mock-DOM subcases exercise malformed, future,
fractional-future and post-expiry grant timestamps, valid offset/microsecond
and indefinite grants, and legacy absent/null metadata. They also assert
request-only Copy initiates no export ticket or download. These checks are
not native Chromium/WCAG, a real consenting-user pilot, verified provider
authorization, a business outcome, or competitor parity.

Coordination: [#326 claim 6556492470](https://github.com/KAVentures/openworkgraph/issues/326#issuecomment-6556492470).


## Truncated UTC date plans disclose omitted selected events (Worker B, 2026-11-01)

The local preview lists at most **31 observed UTC dates** from the selected
period. When the backend explicitly reports \`more_dates_not_listed: true\`,
the date review now shows the **exact number of selected recorded events not
represented by those listed date chunks**: \`canonical_rows\` minus the sum
of validated \`date_chunks[].event_rows\`. This arithmetic is permitted only
after the existing safe-integer, partition, chronological ordering and
truncated-plan validation. It is a selected-event count, **not** a count of
unlisted dates, missed observations, completed cases or true workflows.

Previously the modal told the user to "Continue through the remaining
selected dates", but the date preview has no pagination control. The
replacement explicitly says it **cannot page those dates** and directs the
reviewer to inspect a separately downloaded local package or currently
authorized chronology tools. The full chronology and current permissions
must be checked independently; neither establishes complete capture or
unchanging evidence. The preview still shows diagnostic-only dates without
inviting AI workflow inference, and Copy remains request-only with no ticket,
download, upload or model invocation. Complete plans and legacy plans without
an explicit truncation flag do not invent omitted-event totals.

Two deterministic synthetic Node mock-DOM test functions exercise ordinary
and health-only 32-day histories (31 listed dates, 7 selected events omitted)
plus complete and legacy date plans, verifying exact counts, explicit
non-pagination, uncertainty, and no export on Copy. They do **not** establish
native Chromium/WCAG acceptance, real consenting-user pilot, live-provider
validation, business outcomes, ROI or competitor parity.

Coordination: [#326 claim 6557006223](https://github.com/KAVentures/openworkgraph/issues/326#issuecomment-6557006223).


## Non-paginated date guidance and export coverage caveat (Worker B, 2026-11-01)

The Prepare for AI daily review is a **bounded, non-paginated** display of
observed UTC dates, not a way to advance through every page of the event log.
For ordinary observations it may suggest reviewing one date at a time **only
after the user explicitly inspects and shares a local package**, while requiring
the downstream reviewer to disclose missing dates, unknown capture coverage,
and unverified workflow inferences. Diagnostic-only histories never receive
workflow-review instructions; zero-event selections offer no evidence download.

The download control requests the selected scope **again**, subject to current
authorization, consent, retention, deletion, and intervening capture changes.
It is not an immutable snapshot or a guarantee that the preview and downloaded
file contain identical rows. The existing unchecked detailed-chronology option
produces a summary/operational package, not raw local event rows; raw chronology
requires an explicit opt-in. This is grounded in
`server/exporter.py:build_export_payload` and its
`raw_local_evidence` inclusion gate. Review the actual file before sharing.
No new data leaves the device, no AI model runs, and Copy remains request-only.

Two committed synthetic Node mock-DOM test functions exercise ordinary,
capture-health-only and zero-event previews, including the absence of invented
pagination, unconditional full-period export claims, and clipboard-triggered
ticket/download side effects. All three scenarios passed in an **isolated V8
actual-source mock DOM** (35 assertions), **not** native Node, Chromium, WCAG,
cross-platform, scale, packaging, a consenting-user pilot, provider validation,
verified business outcome, ROI, or competitor parity. The server, export payload,
consent enforcement and authorization are unchanged.

Coordination: [#326 claim 6557688499](https://github.com/KAVentures/openworkgraph/issues/326#issuecomment-6557688499).


## Mixed daily counts do not classify workflow evidence (Worker B, 2026-11-01)

The Prepare for AI daily breakdown lists **total recorded event rows per UTC
date**, not a per-date partition of ordinary work and capture-health/gap
diagnostics. In a mixed selection, the date list previously appeared beside
ordinary-work review guidance without saying that a particular date might
contain **only diagnostics**. This could overstate evidence for repeated work.

The review now displays the validated **selected-period** ordinary and
capture-health/gap totals together, explicitly warns that the daily totals
cannot classify dates, and asks the reviewer to inspect event-level evidence
before treating a date as workflow evidence. A period containing only ordinary
events needs no mixed-evidence notice; a diagnostic-only period retains its
existing no-workflow guidance. The disclosure neither assigns diagnostic rows
to particular dates nor implies that an ordinary event verifies a complete
workflow, source identity, business outcome or automation permission.

A deterministic synthetic actual-source mock-DOM regression covers a mixed
four-row period (two ordinary and two diagnostic rows), an ordinary-only period
and a diagnostic-only period, each with a conserved one-day date plan. It
asserts the mixed-only disclosure and confirms Copy remains request-only with
no export ticket or download. This is not native Chromium/WCAG, cross-platform
or scale acceptance, a real consenting-user pilot, verified provider state,
commercial ROI or competitor parity. No backend, event classification, consent,
export payload, AI execution or business write changes.

Coordination: [#326 claim 6558368521](https://github.com/KAVentures/openworkgraph/issues/326#issuecomment-6558368521).


## Mixed ordinary-work and capture-health evidence survives request-only Copy

A mixed selected period can include both ordinary observations and capture-health
or gap diagnostics. The daily UTC date plan is **not partitioned by event type**;
a listed day with a positive count may contain only diagnostics. A warning in the
local modal is insufficient if the user copies only the agent-neutral request
into another AI: that downstream reader would lose the caveat. For a validated
mixed preview, **Copy request** now appends the selected ordinary and diagnostic
counts, warns that daily totals are untyped, requires event-level source review,
and prohibits treating diagnostic rows as workflow steps or claiming verified
completion, savings or ROI. It does not attach events or confer access.

Ordinary-only previews preserve the original request verbatim. Diagnostic-only
and zero-event previews retain their stronger insufficient-evidence warning.
Legacy previews without the optional `work_evidence` block use the already
validated event-type partition; a missing daily plan is still unknown coverage.
The warning describes only this selected local preview, not source completeness,
recorder liveness, permission or real-world outcomes. The exported file is a
separate, fresh, consent-checked read and is never sent by Copy.

Regression: `node --test tests/js/analysis_handoff_scope.test.mjs` now checks
that the actual copied request carries mixed 2+2 counts and the event-level
warning, including a legacy 1+2 preview without daily chunks; ordinary-only,
health-only and zero-event cases retain their existing copy semantics. The
synthetic DOM verifies no ticket/download on Copy. Earlier stale test assertions
promising complete downloads or nonexistent date pagination were corrected to
match the current conservative interface wording. These deterministic fixtures
are **not** native browser/WCAG, multi-team pilot, provider validation,
competitor parity or measured ROI evidence.


## Bounded source-category ranking for large selected histories (2026-11-01)

The Prepare for AI review displays **at most eight** reported source categories.
It ranks by selected-event count descending and then by literal source label
ascending, independent of import insertion order. The former implementation
materialized and fully sorted every reported category even though only eight
could be shown. The reviewer now retains a sorted top-eight while scanning
the source map once, with bounded ranking memory and no full-category sort.
It still reports exact omitted category and selected-event totals, distinguishes
rendered-label collisions, and handles own literal `__proto__` and
`constructor` source keys without treating them as inherited identities.
Source labels remain unverified, and omitted categories cannot be individually
inspected in this bounded panel. Canonical events and exported evidence are
unchanged.

Synthetic Node regression `large source maps preserve exact top-eight ranking,
literal keys and omitted totals` uses 2,048 low-volume categories and four
late high-volume or special-key categories, in forward and reversed import
order. It checks deterministic top-eight selection, 2,044 omitted categories
and events, and Copy without an export ticket, download, upload or model call.
This is not a real consenting-user pilot, provider-verified source identity,
competitor parity, commercial ROI, or a substitute for native browser,
accessibility, packaging and large-install CI acceptance.

Coordination: [#326 claim 6559758447](https://github.com/KAVentures/openworkgraph/issues/326#issuecomment-6559758447).
