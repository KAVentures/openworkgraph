# Declared working-calendar analysis

The existing **Processing and waiting time** view can add a user-declared calendar
lens. It keeps the original elapsed metrics visible. Calendar-adjusted intervals
are descriptive calculations over supplied log timestamps; actual labor, staffing,
policy, source completeness and execution authority remain unverified.

Select **Add a declared working-calendar lens**, then choose working days, opening
and closing times, an IANA time zone and optional holiday dates. The weekday selector
accepts any nonempty combination; the UI uses one shared period for the selected
days. The API also supports different or split periods per weekday. Click the existing
analysis button. The calendar table shows scheduled processing and gaps, alongside
the elapsed tables. For a Friday 15:00–16:00 interval followed by a Monday
10:00–11:00 interval, a UTC weekday 09:00–17:00 calendar gives a two-hour scheduled
gap while retaining the 66-hour elapsed gap. A holiday can exclude a recorded
interval; that does not establish that nobody worked. The view reports how many
recorded intervals cross closed time.

Editing lifecycle/end attributes or any calendar setting clears results and
aggregate downloads. A late response for old settings cannot replace a newer
analysis or restore its download. Turning the lens off restores the existing GET
flow. Calendar settings and reports are not saved or synchronized automatically.

**Download calendar and elapsed aggregates** includes activity labels, the declared
schedule, opaque log reference/counts, aggregate metrics and clock/query digests.
Raw events, case IDs, resource fields, source filenames and literal cohort-filter
values are excluded. This is a local download, not an AI-context grant. Activity
labels and schedule dates remain literal user-supplied data, not anonymization.

## API and compatibility

`GET /v1/event-logs/{id}/waiting-time` keeps its existing response and behavior.
The optional read-only `POST` at the same path accepts strict JSON:

```json
{
  "mode": "end_attribute",
  "end_attribute": "finished",
  "working_calendar": {
    "timezone": "UTC",
    "hours": {
      "mon": [["09:00", "17:00"]],
      "tue": [["09:00", "17:00"]],
      "wed": [["09:00", "17:00"]],
      "thu": [["09:00", "17:00"]],
      "fri": [["09:00", "17:00"]]
    },
    "holidays": ["2024-01-08"]
  }
}
```

`mode` defaults to `auto`; `lifecycle_attribute` defaults to
`lifecycle:transition`. Case type and existing whole-case cohort filters remain
query parameters, applied to both clocks. Omitted/null `working_calendar` returns
the legacy response. Unknown fields, invalid types, repeated JSON keys, nonfinite
values and oversized bodies are refused. Dashboard session authorization is
required before body parsing; AI bearer credentials do not expose imported logs.
The calculation runs in the existing thread pool under the workspace lock.

An explicit calendar adds `working_calendar` with parallel activity/gap statistics,
union-busy flow efficiency, outside-calendar instance counts and provenance:

- `calendar_sha256` hashes normalized zone, weekly periods, holidays and boundary policy.
- `utc_opening_windows_sha256` hashes the actual merged UTC opening intervals,
  clipped to the selected retained interval range; `utc_opening_windows` is their count.
- `case_filter_sha256` and `instance_settings_sha256` identify query assumptions
  without exporting literal filter values.

These hashes identify calculation inputs, not verified business authority or a
signed source receipt. A changed time-zone database can change the UTC-window
digest even when the declared configuration is unchanged. The log reference is a
collection reference, not an independently verified event-content snapshot.

## Clock and bounds

The implementation reuses OWG's simulation `Calendar` validation and time-zone
rules. For each local opening period, an ambiguous opening uses its earliest
occurrence and an ambiguous closing uses its latest occurrence. Each period is a
continuous interval in real UTC time. Overlapping/touching intervals are unioned
once. A skipped boundary moves to the clock jump; a period wholly inside a skipped
hour has no real length. For example, New York's fall-back 01:15–01:45 period maps
to 05:15–06:45 UTC under this explicit policy: 90 continuous minutes. This is not
pointwise filtering of repeated local wall-clock labels.

A cumulative clock over the merged UTC intervals maps start, end, previous end
and union-busy boundaries with four ASOF lookups. It avoids expanding every
instance against every calendar day. Unknown processing stays null; the first
instance's absent preceding gap stays null. Recorded overlap counts once as busy.
Cases with no scheduled span have undefined calendar efficiency and an explicit
count, rather than a fabricated zero-denominator ratio. No current wall-clock
value is used to invent an end for an unmatched start.

Bounds are one million derived instances, 3,660 local dates including boundary
padding, recorded UTC years 1900–2100, four periods per weekday and 400 holiday
dates. Exceeding a bound refuses the calendar calculation without partial or
sampled results; the ordinary elapsed view remains available. There is one
calendar per analysis, with no per-person performance scoring, actual staffing
model, policy enforcement or automatic ROI/scenario adoption.

## Verification

Independent interval-intersection and explicit UTC oracles cover weekends,
holidays, overlapping work, zero/unknown durations, closed-only cases, DST gaps
and repeated boundaries, merged openings, pre-1970 arithmetic and microseconds.
Tests retain canonical source histories and legacy/default response equality,
whole-case filtering and dashboard-only strict HTTP contracts. The browser covers
explicit opt-in, visible elapsed comparison, holiday changes, minimized downloads,
held old-calendar responses and WCAG/mobile.

`scripts/benchmark_working_calendar.py` checks 100,000/1,000,000 lifecycle or
end-attribute events across two years and holidays against independent expected
counts, gaps, busy/span totals, closed-case counts and unchanged legacy results.
These are synthetic correctness/scale checks, not a latency SLA, real-work accuracy
or customer rollout certification. Reference: existing OWG `Calendar` plus
[Python zoneinfo](https://docs.python.org/3/library/zoneinfo.html).
