# Event-time object state review (R24)

Open **Event-time object state check** in the object-centric map, or open
**Object binding continuity** from the workspace. Both use the selected
retained OCEL log even if it has no supplied object-to-object assertions.
In the map, select a result status to page matching events and open an event
to page all its retained object witnesses. Changing the check clears earlier
results; a changed log requires rerunning the check.

Enter an exact activity, object type, attribute name and expected retained
string (including an empty string if appropriate). Select equality or
inequality, and all or any linked objects. Optionally restrict the event-object
qualifier to the exact literal value, including the empty qualifier. Every
recorded event of the selected activity is included, including events with
no selected object. Duplicate qualifier links to the same object count once.

The map's optional **Find** controls search stored activities across the log,
or timed attribute names, values and event-link qualifiers for the selected
object type. Value lookup also uses the exact attribute name. Matching is
case-sensitive and literal; a blank search lists values, including an empty
value or qualifier. Value matches cover retained history, including values
from times other than the event being checked. Select a result to copy its
exact stored text into the check. Labels the checker cannot accept are shown as unavailable. Lookups
are local and owner-only; later pages use the reviewed append revision.
When a selected or loaded value contains carriage returns, the form preserves
the exact stored text until the value is edited, even though the text area
displays normalized line breaks.

Use **Save as new check** to keep the current definition with the retained
log, then **Load saved check** to restore it without running it. Saved
definitions can contain exact recorded values and stay local with the log.
Each log keeps up to 50 named checks. Updates and deletion require the loaded
definition's revision; if another edit wins, reload before changing it.
Deleting the retained log also deletes its saved checks. Saved definitions
contain settings, not event or witness results, and loading one does not
authorize any action.

## Time and uncertainty

The latest retained attribute timestamp at or before the event applies.
A value recorded at the event timestamp is included. The OCEL initial
timestamp 1970-01-01 is treated as the stored initial convention. No
first-listed current-value fallback is used. Missing history, a timestamp
before the first recorded value, and conflicting values at the latest
timestamp are separately reported as unknown. A later unambiguous value
resolves a previous ambiguity for later events. No numeric or boolean
coercion is performed: comparisons use the retained string representation.

For all objects, any known mismatch makes the event not matched; otherwise
unknown dominates a match. For any object, a known match makes the event
matched; otherwise unknown dominates a mismatch. Both modes report
no_linked_object separately instead of vacuous success.

The result contains exact event and distinct event-object counts over the
retained selection, and a status-filterable evidence table. Each event
includes all object outcome counts and up to 20 literal object witnesses,
with an explicit truncation flag. The separate witness endpoint pages the
complete retained set for a reviewed event using literal object-ID cursors.
Pagination of events is chronological with event
ID as deterministic tie-breaker, not an inferred temporal order. Later pages
require the retained append revision; append invalidates an older revision.
Rerun after changing filters. The report does not establish historical
source completeness, typed policy semantics, actual policy compliance,
causal results or execution rights.

## API

POST /v1/event-logs/{log_id}/object-state-check

Local dashboard session required; no-store response. Strict JSON, 16 KiB.
Example:

```json
{"activity":"Pay","object_type":"order","attribute":"status",
 "expected_value":"approved","operator":"eq","quantifier":"all",
 "event_qualifier":"","status":"unknown","limit":30}
```

Omit event_qualifier to use every qualifier. Omit status to page all events.
Use next_offset and revision as offset and expected_revision on a later page.
Limits: 200,000 selected events, 1,000,000 distinct selected event-object
links, 2,000,000 selected history rows, 1–100 events per page.
An exceeded work bound refuses the analysis instead of presenting sampled
counts as exact. No new persistent state, model change or provider calls.

For full object evidence, POST the same check settings to
`/v1/event-logs/{log_id}/object-state-check/witnesses` with the reviewed
`event_id` and `expected_revision`. Use `next_after` as `after` to request
another page. A result status filter, if supplied, must include the event;
the endpoint refuses stale revisions and unrelated events. Witness requests
recompute the same bounded check and can therefore take time on large logs.

The owner-only label endpoint is
`POST /v1/event-logs/{log_id}/object-state-check/labels`. Its `kind` is
`activity`, `attribute`, `value` or `qualifier`; scoped kinds need
`object_type`, and `value` also needs `attribute`. The optional `search` is a
literal substring. Pass a non-null `next_after` (including an empty string)
as `after` with `expected_revision` for another page. `checkable` marks
whether each returned label can be used by the checker.

Saved definitions use the owner-only `GET` and `POST`
`/v1/event-logs/{log_id}/object-state-check/saved` endpoints. A saved ID can
be read, updated with `expected_revision`, or deleted with an
`expected_revision` query parameter under the same path. Definitions contain
only check settings; result filters, page offsets, results and source event
data are not stored in them.

## Analyze one exact object

Use **Find an object ID** to search or browse objects linked to the selected
activity, type, date window, optional event, and exact qualifier. Search is
literal and case-sensitive. Results are ordered by ID, and subsequent pages
are pinned to the append revision. Unlinked objects do not appear in this
scoped lookup; an exact ID can also be entered directly.

Optionally enter an exact object ID for the selected object type before running
the check. Only events of the stored activity linked to that object (and the
exact qualifier, if set) are included. The event bound applies after this
selection. The check evaluates that object alone, so results do not describe
other objects linked to the same events. The event-ID lookup honors this object
scope, and an optional exact event ID and date window further restrict it.

The object ID is temporary, excluded from saved and transferred definitions,
and included in scoped CSV metadata. All downstream evidence, timelines and
distributions use the reviewed revision and the same object scope.

## Analyze one exact event

Use **Find an event ID** to browse or search exact IDs for the stored activity
and date window. Search is case-sensitive and literal; pages are ordered by ID
and pinned to the log revision. A changed log requires restarting the lookup.
Each result includes the event timestamp, and selecting it fills the scope field.

Optionally enter an exact event ID before running the check. The activity,
UTC date window, and exact ID jointly define the analysis scope, and the event
limit applies after this selection. This differs from the result event-ID
filter, which narrows already analyzed results and does not bypass analysis
limits. A scoped event still uses its complete linked-object evidence and
retained history. The scope follows the reviewed revision into timelines,
distributions, witnesses, and CSV metadata. It is temporary and is neither
saved nor included in transferred check definitions.

## Review recorded values at events

**Recorded values at events** groups the selected event-object links by their
object result and exact value at the event time. Empty strings remain distinct
from unknowns. Missing history, future-only history, and conflicting values are
separate unknown categories; events with no linked object add no links. Counts
are links, not distinct objects. The reviewed revision and current date,
status, and event-ID filters apply to the table and complete CSV export.

Select **Inspect value group** to find events with at least one object in that
group. Other linked objects remain in each event's evidence and object CSV,
so reviewers retain the full event context. The event's result status is still
computed from all selected linked objects. The temporary group filter combines
with the event and date filters, applies to timelines and exports, and is not
saved in check definitions. Clear it explicitly to return to all events.
Distribution counts under a group filter include all links of the matching
events, including links outside that group. CSV metadata records the group
filter and distinguishes an empty exact value from no value.

The distribution refuses more than 1,000 groups or any recorded value longer
than 4,096 characters, rather than silently truncating. The CSV includes the
check settings and filters, escapes spreadsheet formulas, and may contain
sensitive recorded values. Owner-only endpoints are `POST
/v1/event-logs/{log_id}/object-state-check/distribution` and
`/distribution/export`. Both recompute the bounded check; the export includes
all matching groups and does not accept custom pagination.

## Review outcomes over time

After a check, **Outcome timeline** groups matching events by UTC day, week
(Monday start), or month. Counts use the current date, status, and exact event
filters and are pinned to the reviewed append revision. Only non-empty periods
are displayed. The timeline recomputes the bounded analysis, and more than 1,000
non-empty periods is refused with a prompt to choose a larger period or window.
It does not establish source completeness or policy compliance.

Selecting **Inspect period** intersects that period with the current date
window and opens the corresponding event evidence at the same reviewed
revision, retaining status and exact event filters. The complete timeline CSV
includes the check settings, filters and revision, even for an empty result;
formula-like cells are escaped for spreadsheet review.

The owner-only endpoint is `POST
/v1/event-logs/{log_id}/object-state-check/timeline`; it accepts the check
settings, `expected_revision`, `bucket`, and bounded `offset` and `limit`. The owner-only
`/timeline/export` endpoint downloads all matching periods and does not accept
custom pagination.

## Find a specific event in a result

After running a check, apply an exact event ID to narrow the displayed result
and both worklists. It combines with the result-status filter and the UTC date
window; summary counts continue to describe all selected events before result
filters. Filtering happens after the bounded check and does not bypass its event,
link, or history limits. Witness requests enforce the same filters. The event ID is a temporary
review filter and is not stored in saved or portable definitions. Worklist
metadata records the applied ID, including when no rows match.

## Restrict the event review period

Optional UTC calendar dates select events from the inclusive start date to the
exclusive end date. Either boundary may be omitted. The end must follow the
start when both are supplied. The event limit applies after this selection;
object history before the start remains available to establish each selected
event's recorded state. Saved and transferred definitions preserve the dates,
and worklist rows include both boundaries. Label lookup and transfer preview
still search the whole retained log, so presence there does not imply presence
inside the selected period.

## Transfer definitions between logs

Open **Transfer a check definition** to download the current named check as
JSON, or select a JSON definition and preview it against the destination log.
The versioned file contains only the name and check settings, including the
expected value; treat it as sensitive when that value is sensitive. It does
not contain event IDs, object IDs, or results. The file limit is 64 KiB.

Preview reports whether the exact activity, object type, timed attribute,
expected value and relevant event-object link labels occur in the retained
log at the reported append revision. These checks are advisory: label presence
can change, and it does not establish source completeness or compliance.
Loading a preview fills an unsaved draft; saving and running remain explicit.

The owner-only endpoint is `POST
/v1/event-logs/{log_id}/object-state-check/template/preview`. Its body must
contain exactly `format` (`openworkgraph.object-state-check.v1`), `name`, and
`definition`. Preview neither saves nor runs the definition.

## Complete review worklists

The object-map checker can download all events in the chosen result status or
all corresponding event-object outcomes. Both CSVs cover the entire filtered
selection, not just the current page, and include the exact check settings and
reviewed append revision in a `check` metadata row, including when no events
match. `X-OWG-Rows` counts evidence rows and excludes that metadata row. The
object worklist includes an
`event_without_object` row for each event with no selected object. Its
`recorded_value_present` field distinguishes an empty recorded value from an
unknown value; `qualifier_present` distinguishes an empty exact qualifier
from no qualifier restriction.

These are spreadsheet-review files: formula-like cells are escaped, so they
are not a lossless interchange format. They contain event and object IDs and
recorded values. A stale revision or an exceeded analysis bound fails the
download rather than producing partial results. A worklist is limited to
512 MiB of UTF-8 CSV; exceeding the limit also fails without a download.
Files are prepared locally and the temporary file is removed after the response.

The owner-only POST endpoint is
`/v1/event-logs/{log_id}/object-state-check/export`, using the check settings,
reviewed `expected_revision`, optional `status`, and `kind` of `events` or
`objects`. An export always covers the full filtered selection.

The test fixture covers exact-time updates, conflicts and later resolution,
missing attributes and objects, duplicate qualifier associations, all/any
semantics, pagination and authentication. Real customer acceptance and
large-scale latency are not established by the fixture.
