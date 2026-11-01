# R9 — UTC case intake/completion heatmaps and elapsed-time bands

The existing **local, session-authenticated, typed event-log analysis API**
supports five new case-level dimensions. They compose with the existing
`count`, `cases`, `share`, median/p90/average duration, cohort filters,
save/reload and bounded two-dimensional grouping. This is a usable way to
inspect *when supplied cases begin or finish* and how their elapsed durations
are distributed, without raw SQL, new source access or person-level scoring.

| Dimension | Meaning |
| --- | --- |
| `start_weekday_utc` | UTC weekday of each case's first recorded event, prefixed with ISO weekday 1 (Monday) through 7 (Sunday) |
| `start_hour_utc` | UTC hour of the first event, `00`–`23` |
| `end_weekday_utc` | UTC weekday of the last recorded event |
| `end_hour_utc` | UTC hour of the last recorded event, `00`–`23` |
| `duration_band` | Elapsed first-to-last-event duration in four nonoverlapping half-open bands: under 1 hour; 1–under 24 hours; 1–under 7 days; 7 days or more |

**Example:** With a selected retained log, POST to the existing
`/v1/event-logs/{log_id}/analysis` endpoint using the usual
`OWG-Session` local dashboard authorization:

```json
{
  "level": "cases",
  "dimensions": ["start_weekday_utc", "start_hour_utc"],
  "measures": [
    {"fn": "count"},
    {"fn": "median", "of": "duration_seconds"}
  ],
  "order": "dimensions",
  "limit": 168
}
```

The result is a bounded day-by-hour table (up to 7 × 24 cells) with counts
and the median elapsed duration in seconds. Missing day/hour combinations
have **no row**; do not interpret them as complete upstream coverage.
A second analysis can group by `end_weekday_utc` and `end_hour_utc` to
inspect completion patterns. Grouping by `duration_band` exposes the
distribution of recorded case elapsed time. These dimensions are also valid
in the existing saved-analysis definitions.

All timestamps are normalized to UTC by the existing importer; these
dimensions are **not local business-hours or shift calendars**. Overnight
cases can start and end on different UTC days. Duration is wall-clock elapsed
time between first and last observed events, **not** labor time, processing
time, source-authoritative case duration or a productivity metric. A
single-event case has zero observed duration; it is not evidence that the
business process was instantaneous. Cases with unobserved source history can
be partial, and the result cannot establish how many upstream cases are
missing.

The existing two-dimension/four-measure/500-row limits, cohort and case
notion, explicit authorization, safe SQL compiler, retention and deletion
rules remain unchanged. No new API route, raw event export, background sync,
identity access, telemetry or business action is introduced. Saved definitions
are local to the selected log and are deleted with it.

Synthetic regression coverage checks UTC offset normalization, midnight
crossing, two-dimensional grouping, chronological weekday/hour sorting,
all exact duration-band boundaries, filters, saved definitions and rejection
of SQL-like or wrong-level dimensions. This is not real-provider or
consenting-customer acceptance, and does not establish competitor parity.


## IANA-local business-time view (explicit opt-in per analysis)

For time-of-week charts in the analyst's actual operational time zone, use
`start_weekday_local`, `start_hour_local`, `end_weekday_local`, and/or
`end_hour_local`, **and explicitly provide** `time_zone` with a valid IANA
zone name. This is a *display/grouping conversion only*, not a change to
stored timestamps, duration, source coverage, consent or log identity.

```json
{
  "level": "cases",
  "dimensions": ["start_weekday_local", "start_hour_local"],
  "time_zone": "Europe/Stockholm",
  "measures": [{"fn": "count"}, {"fn": "median", "of": "duration_seconds"}],
  "order": "dimensions",
  "limit": 168
}
```

The query interprets stored UTC timestamps as instants, then groups them by
their IANA local weekday/hour. Spring daylight-saving transitions can skip a
local hour; autumn transitions can repeat the same wall-clock hour, which
**combines two distinct UTC hours into one bin**. Neither a zero count nor a
repeated hour establishes a staffing requirement or source completeness.
Elapsed durations remain UTC-instant differences, never wall-clock subtraction.

The zone is validated before reading a log and passed as a bound SQL
parameter; it is never interpolated into SQL. Local dimensions without
`time_zone`, unknown zones, and zones supplied to UTC-only/event analyses
are refused rather than silently changing interpretation. Saved analyses
preserve the exact zone name. Existing UTC dimensions retain their original
meaning and need no zone. These results do not infer the user's local zone
from their computer or location, and they do not rate employees.

Synthetic tests cover both DST transitions, midnight/weekday crossing,
save/reload, malicious zone refusal, and mixed attribute/zone parameter
ordering. Native cross-platform CI and real consenting source validation
are separate acceptance gates.

## Event-volume heatmap in an explicit IANA local zone

To inspect **when events were observed** (rather than only when cases
started/ended), use the event-level `weekday_local` and `hour_local` dimensions.
The existing authenticated analysis endpoint supports this spec:

```json
{
  "level": "events",
  "dimensions": ["weekday_local", "hour_local"],
  "time_zone": "Europe/Stockholm",
  "activity_in": ["Approve"],
  "measures": [{"fn": "count"}, {"fn": "cases"}, {"fn": "share"}],
  "order": "dimensions",
  "limit": 168
}
```

This returns up to 168 populated local weekday/hour bins. `count` is observed
events, `cases` is distinct case IDs within each bin, and `share` is the
fraction of matching events represented by that bin; one case can contribute
events to multiple bins. An activity filter is optional and restricts the
population before grouping. The selected zone and filters survive existing
saved-analysis save/reload. The original UTC event `weekday` and `hour`
dimensions retain their prior behavior and require no `time_zone`.

Spring DST jumps can leave local hour bins absent. Autumn DST folds combine
two distinct UTC hours into one local bin. The conversion is performed only
for grouping; source timestamps and elapsed durations remain unchanged.
Absent bins are not proof of zero upstream events or full source coverage.
No employee-level performance scoring, shift staffing recommendations,
business-hours calendar or provider-authoritative activity claims are made.

The IANA zone is explicitly required for local dimensions, validated before
querying, and bound as a SQL value. The analysis continues to use the same
typed compiler, event/case permissions, bounded result limits, log retention,
revocation and export behavior. Synthetic regressions exercise DST, event
counts versus distinct cases, activity filters, attribute/zone SQL parameter
ordering, UTC compatibility and saved definitions. Native CI and real
consenting source acceptance remain separate gates.

## Compare activity or case attributes against all 168 weekday-hour buckets

The typed analysis builder allows **two dimensions** per query. To compare an
activity against a whole week of hourly bins without requesting a third
dimension, select one **composite weekday-hour dimension** and one business
dimension. This is a single, sortable text key in the form
`1 Monday 00` through `7 Sunday 23`; it is not a timestamp, time interval,
calendar, staffing measure, or proof of complete observation.

| Level | UTC composite | Explicit IANA-local composite |
| --- | --- | --- |
| Events | `weekday_hour_utc` | `weekday_hour_local` |
| Cases, first observed event | `start_weekday_hour_utc` | `start_weekday_hour_local` |
| Cases, last observed event | `end_weekday_hour_utc` | `end_weekday_hour_local` |

For example, compare observed activity mix across local hours:

```json
{
  "level": "events",
  "dimensions": ["weekday_hour_local", "activity"],
  "time_zone": "Europe/Stockholm",
  "measures": [{"fn": "count"}, {"fn": "cases"}, {"fn": "share"}],
  "order": "dimensions",
  "limit": 500
}
```

The 500-row limit is unchanged. With many activities, 168 × activity
combinations can exceed it: `rows_truncated` must be checked, and a narrower
`activity_in` filter or cohort is needed for an exhaustive view. `share`
describes the proportion of **selected observed events**, not the fraction of
time spent working or cases completed. The second dimension can instead be a
declared `attribute:<name>` (or a case dimension such as `first_activity`).

A fall daylight-saving fold combines two real UTC hours in one local bucket;
a spring gap has no local 02 hour. The source instants remain UTC, the zone is
explicitly validated and bound as a SQL parameter, and the original UTC
dimensions retain their semantics. Composite local dimensions are saveable
with their zone. No source sync, employee rating, business-hours inference,
hidden completeness assumption, or additional authorization is introduced.

Synthetic tests cover activity × bucket counts, distinct cases and shares,
DST fold/gap, case start/completion × first/last activity, save/reload,
wrong-level dimensions and missing/irrelevant zone refusal. Native CI and
consenting real-source acceptance remain separate requirements.
