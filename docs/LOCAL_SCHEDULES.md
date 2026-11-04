# Scheduled jobs

OpenWorkGraph can repeat two jobs on a local schedule. Nothing is scheduled by
default.

| Job | What a run does |
|---|---|
| `retained_jira_profile` | Runs an existing retained Jira profile, exactly like **Run** in the dashboard. The profile's own read/retention consent, site-grant check and same-account check apply on every run. |
| `event_log_signals` | Re-evaluates the saved signal rules of an event log, so the signal inbox reflects current data (`scheduled_checks_enabled: true` in the inbox). |

Behavior:

- Add one in **Privacy → Scheduled jobs**, or with `POST /v1/schedules`
  `{kind, target_id, interval_minutes, confirm_scheduled_runs: true}`. The interval
  is 15 minutes to 7 days. There is one schedule per job.
- Jobs run one at a time on this computer while OpenWorkGraph is running. There is
  no cloud scheduler, no delivery to other people and no business writes.
- A failed run backs off exponentially (interval × 2ⁿ, at most 24 hours, and never
  sooner than a provider's own retry-after). Five failures in a row pause the
  schedule; **Resume** resets the count.
- Pausing recording skips scheduled Jira reads (outcome
  `skipped_recording_paused`).
- If the profile or event log is deleted, its schedule is removed at the next due
  run.
- Run history keeps the time and a fixed outcome code only (for example
  `failed:jira_account_binding_changed`), never source content.

API (dashboard session): `GET /v1/schedules`, `POST /v1/schedules`,
`POST /v1/schedules/update` `{schedule_id, enabled?, interval_minutes?}` and
`POST /v1/schedules/delete` `{schedule_id}`.
