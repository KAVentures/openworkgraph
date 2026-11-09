# When work happens

**When work happens** in the event-log workspace shows a weekday × hour grid of events (all,
or one activity) and of new cases (first events), in the time zone you choose. Daylight
saving is included, and the browser's own time zone is the default. It also gives the
share of events outside working hours (default 08:00–18:00, Monday to Friday) and the
busiest weekday hours.

Timestamps are stored in UTC, so the same event can fall on a different day in a different
time zone: an event at 23:30 UTC on a Sunday is 00:30 on Monday in Stockholm. Counts are of
when events were **recorded**. System-generated events can cluster at batch times, and
recorded time is not proof of when people worked.

## API (dashboard session only)

`POST /v1/event-logs/{log_id}/time-patterns` with
`{"time_zone": "Europe/Stockholm", "activity": "...", "work_start_hour": 8, "work_end_hour": 18, "work_days": [1,2,3,4,5], "cohort": {...}, "case_type": "..."}`.
All fields are optional. `events` and `case_arrivals` are 7 × 24 grids, Monday first.
