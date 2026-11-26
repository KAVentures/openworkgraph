# Stuck open cases

**Stuck open cases** in the event-log workspace lists the open cases that have waited much
longer than usual after their latest activity.

## How "usual" is decided

For every activity, the log's own history gives how long cases waited after it before their
next event. An open case is **stuck** when the time since its latest event, measured to the
end of the log, is longer than the chosen percentile of those waits (90% by default; 75% and
95% are offered). An activity needs at least 20 observed waits; open cases at an activity
with less history are counted as **not judged**, so silence is never mistaken for "fine".

A case is **open** when its latest activity is not one of the given end activities. With no
end activities, a case is open when its latest activity is one that other cases continued
from.

## The report

- How many judged open cases look stuck, and how many could not be judged.
- The stuck cases, most overdue first: latest activity, time idle, the usual (median) wait
  after that activity, the threshold it passed, and events so far.
- Where they wait: open and stuck cases per latest activity.

A stuck case is a prompt to look, not a finding that anyone did anything wrong. The export may
simply end before the next step was recorded.

## API (dashboard session only)

`POST /v1/event-logs/{log_id}/stuck-cases` with
`{"end_activities": [...], "percentile": 0.9, "min_support": 20, "limit": 50, "cohort": {...}, "case_type": "..."}`.
All fields are optional.

`POST /v1/event-logs/{log_id}/stuck-cases.csv` takes the same body and returns **every** stuck
case, not only the first `limit`, as a CSV worklist, most overdue first. The dashboard's
**Download all stuck cases (CSV)** button calls it with the current settings and case filter.

Columns: `case_id`, `latest_activity`, `idle_seconds`, `usual_wait_seconds`,
`stuck_after_seconds` (the percentile threshold), `times_stuck_threshold`, `events_so_far`,
`case_age_seconds` and `as_of` (the log's last event, which idle time is measured to). Cells that
a spreadsheet would read as a formula start with `'`. The `X-OWG-Stuck-Cases` header counts
stuck, judged and not-judged open cases.
