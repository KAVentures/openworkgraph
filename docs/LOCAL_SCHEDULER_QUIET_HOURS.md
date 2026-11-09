# R10 — Local scheduler quiet hours

**Status:** opt-in local feature; no change to schedules without a configured
quiet-hours window. This builds on the existing owner-authenticated recurring
jobs and the scheduled backup user journey from PR #696.

## Owner experience

Open OWG's local dashboard, **Privacy → Scheduled jobs**. A new checkbox,
**Only run outside quiet hours**, reveals a start time, end time and named
IANA timezone. The checkbox is initially **off**.

For a new job, select it, provide its target and interval, optionally enable
quiet hours, then use the existing confirmation. For a scheduled local backup,
the separate acknowledgement that the archive is **unencrypted** remains
mandatory, whether or not quiet hours are chosen.

For an existing job, choose a quiet window above the schedule list and click
**Apply quiet hours** or **Update quiet hours** on that exact row. Click
**Clear quiet hours** to turn off deferral for that job. Pause/resume/delete
continue to work normally. Quiet windows never retroactively trigger missed
jobs or negate a source connector's read and retention permissions.

Example JSON for `POST /v1/schedules` (owner dashboard session required):

```json
{
  "kind": "event_log_signals",
  "target_id": "log_0123456789abcdef",
  "interval_minutes": 60,
  "confirm_scheduled_runs": true,
  "quiet_hours": {
    "timezone": "Europe/Stockholm",
    "start": "22:00",
    "end": "07:00"
  }
}
```

`POST /v1/schedules/update` with
`{"schedule_id":"sch_...","quiet_hours":null}`
clears the quiet window. Omitting `quiet_hours` preserves the current
configuration. The owner can modify one job without changing the others.
An AI/MCP bearer cannot view or edit the schedules.

## Scheduling contract

- Exactly the keys `timezone`, `start`, and `end` are accepted.
  The timezone must be a resolvable IANA name (e.g. `Europe/Stockholm`,
  `America/New_York` or `UTC`). Times are strict `HH:MM` 24-hour
  strings; a full-day window (`start == end`) is refused.
- `start < end` means the daytime half-open interval
  `[start, end)`. When `start > end`, the window crosses midnight.
- The scheduler checks the **actual UTC instant**, converts it to local
  time with `zoneinfo`, and defers a due job to the first future UTC
  minute outside the window. The bounded UTC search is safe across
  nonexistent spring times and both instances of repeated autumn times.
- A quiet-hour deferral updates the job's `next_run_at` and local
  `last_deferred_at`/`last_deferred_until` metadata only. It does **not**
  run a source read/backup, update `last_run_at`, create a fake success or
  failure, advance the failure counter, add history, send notifications or
  authorize work. An invalid persisted window fails closed with a fixed
  diagnostic and existing failure backoff.
- Disabled jobs never run. Removing a deleted source target still works.
  The existing serial-job lock, retry-after handling, failure pause and
  minimum interval continue unchanged (15 minutes for signals/Jira;
  at least one day for scheduled local backup).

This is **not** a remote alert delivery, quiet notification recipient
service, real-time SLA monitor or guaranteed execution at a wall-clock
instant; the existing scheduler checks on a 30-second loop, and a machine
that is sleeping or off runs nothing until OWG resumes. One missed run is
not backfilled multiple times.

## Tests and limitations

`tests/test_local_scheduler_quiet_hours.py` exercises exact off-by-default
behavior, strict IANA/time validation, overnight deferral, daytime edge
bounds, Stockholm 2026 spring gap and autumn repeated hour, no fabricated
history/runner call, update/clear, invalid persisted configuration, and
real local dashboard-session API versus bearer denial.

`tests/browser/scheduled_quiet_hours.cjs` exercises the actual Privacy
UI with an isolated synthetic local app: invalid-window refusal,
new unencrypted backup acknowledgement, exact timezone settings,
updates, clear, pause/remove, no eager snapshot, WCAG and mobile.

The dedicated existing (feature-owned) path-filtered browser workflow
includes this test alongside the other recovery journeys. The feature
remains **draft until full native and actual browser CI passes on its own
exact head**. It does not justify claims of end-to-end company-scale
operational monitoring or production backup reliability.
