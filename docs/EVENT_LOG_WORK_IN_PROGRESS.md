# Work in progress over time (R8)

**Work in progress over time** in the event-log workspace shows, per day or week, how many
cases arrived, how many completed, and how many were still open at the end of the period.

- A case **arrives** at its first event.
- A case **completes** at its last event when that event is one of the given end
  activities. With no end activities, every case completes at its last event.
- **Open at the end** of a bucket counts the cases that had arrived and not yet completed.

At most 400 buckets are shown; use weeks or a cohort for longer logs. The chart draws open
cases, arrivals and completions, and the table lists every bucket.

## Little's law

Over the log's span, the time-averaged number of open cases should be close to the arrival
rate times the mean cycle time of completed cases. The report gives both numbers and their
ratio. A ratio far from 1 usually means the export starts or ends mid-flow (cases that began
before the log, or are unfinished at its end), or that many cases never reach an end
activity. That count is shown too.

Counts describe the supplied log. Open cases at its end may simply be unfinished in the
export. This is not a capacity plan or a rating of anyone.

## API (dashboard session only)

`POST /v1/event-logs/{log_id}/work-in-progress` with
`{"bucket": "day"|"week", "end_activities": [...], "cohort": {...}, "case_type": "..."}`.
All fields are optional.
