# Work in progress over time (R8)

**Work in progress over time** in the event-log workspace shows, per day or week, how many
cases arrived, how many completed, and how many were still open at the end of the period.

- A case **arrives** at its first event.
- A case **completes** at its last event when that event is one of the given end
  activities. With no end activities, every case completes at its last event.
- **Open at the end** of a bucket counts the cases that had arrived and not yet completed.

At most 400 buckets are shown; use weeks or a cohort for longer logs. The chart draws open
cases, arrivals and completions, and the table lists every bucket.

Select a count in the table to see its cases, when each started, and any completion recorded
before the bucket ended. **Open case** shows the case's event history. The chart and case list
both use the active workspace case filter. Buckets use UTC, with weeks beginning on Monday.
Events exactly at the next bucket's start belong to that next bucket.

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

`POST /v1/event-logs/{log_id}/work-in-progress/cases` uses the same settings plus
`bucket_start` (the bucket's UTC date as `YYYY-MM-DD`) and `group` (`arrivals`, `completions`
or `open_at_end`). It returns the exact group total and a page of cases ordered by literal key.
The optional `limit` is 1–100 and defaults to 50. Send the returned `next_after` as `after`
to continue. Each page reflects the current log.
