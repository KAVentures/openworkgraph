# When work happens

**When work happens** in the event-log workspace shows a weekday × hour grid of events (all,
or one activity) and of new cases (first events), in the time zone you choose. Daylight
saving is included, and the browser's own time zone is the default. It also gives the
share of events outside working hours (default 08:00–18:00, Monday to Friday) and the
busiest weekday hours.

Switch the grid between **Recorded events** and **Case arrivals**, then select a populated cell
to see its cases. **Inspect outside-hours events** uses the selected working days and hours.
The event view respects the activity picker. Arrivals use the first event of each case in the
active workspace case filter, regardless of the activity displayed in the event view.

Timestamps are stored in UTC, so the same event can fall on a different day in a different
time zone: an event at 23:30 UTC on a Sunday is 00:30 on Monday in Stockholm. Counts are of
when events were **recorded**. System-generated events can cluster at batch times, and
recorded time is not proof of when people worked.

## API (dashboard session only)

`POST /v1/event-logs/{log_id}/time-patterns` with
`{"time_zone": "Europe/Stockholm", "activity": "...", "work_start_hour": 8, "work_end_hour": 18, "work_days": [1,2,3,4,5], "cohort": {...}, "case_type": "..."}`.
All fields are optional. `events` and `case_arrivals` are 7 × 24 grids, Monday first.

`POST /v1/event-logs/{log_id}/time-patterns/cases` uses the same settings and a `group`.
For `events` or `case_arrivals`, provide `weekday` (ISO 1–7) and `hour` (0–23).
`outside_working_hours` uses the working schedule. The response includes the distinct case total
and, for event groups, the number of distinct matching events. It returns cases in literal key
order with up to `limit` rows (1–100, default 50). Send `next_after` as `after` for the next page.
Matched timestamps are in UTC, including when two offsets fall in the same local hour.
