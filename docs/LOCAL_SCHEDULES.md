# Scheduled jobs

OpenWorkGraph can repeat explicitly selected local jobs. Nothing is scheduled by
default.

| Job | What a run does |
|---|---|
| `retained_jira_profile` | Runs an existing retained Jira profile, exactly like **Run** in the dashboard. The profile's own read/retention consent, site-grant check and same-account check apply on every run. |
| `event_log_signals` | Re-evaluates the saved signal rules of an event log, so the signal inbox reflects current data (`scheduled_checks_enabled: true` in the inbox). |
| `event_log_report` | Saves a copy of an event log's self-contained HTML report in the local data folder (`event_log_reports/<log_id>/`), keeping the newest 10. Deleting the log deletes the copies and the schedule. |

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

Intervals and failure backoff begin when a run finishes, so long-running jobs
cannot immediately repeat or shorten a provider's retry delay. Pausing or editing
a schedule does not cancel a job already running. Its completion still appears
in history, but cannot overwrite controls changed during that run or count an
old failure toward a newly resumed schedule. Deleting a running schedule prevents
its completion from recreating it.


## Pinned scheduled signal rules (R10)

A newly created `event_log_signals` schedule now pins the **current saved
rule set**, including each immutable rule ID, condition, cohort, and other
declarative fields. The local schedule file retains only a SHA-256 digest and
rule count, not the rule names or evidence. At least one saved signal rule
must exist when scheduling. The pin excludes `last_evaluated_at`: normal
evaluation and new event-log data do **not** require reapproval.

If a rule is added, removed or changed, a due schedule **does not evaluate
the changed rules**. It records `paused:signal_rules_changed`, disables
itself with reason `signal_rules_changed_requires_reapproval`, and does not
count this as a failed execution or send anything. The rules are compared and
evaluated under the same event-log lock so a concurrent edit cannot slip
between authorization and evaluation. A schedule saved by an older version
that has no rule-set digest also pauses rather than silently assuming new
consent. The owner can still evaluate signals manually.

The local dashboard shows how many rules were pinned and provides
**Review rules and resume**. First inspect the current saved rules in the
Event Log workspace. The reapproval confirmation then pins the current
definition set; the same operation can resume the schedule:

`POST /v1/schedules/update`

```json
{"schedule_id":"sch_...","enabled":true,"confirm_current_signal_rules":true}
```

The confirmation is intentionally explicit. A normal **Resume** cannot
override the reapproval pause. An API caller can re-pin without resuming
by omitting `enabled`, then resume separately; concurrent edits of the
schedule configuration refuse a stale reapproval. If a rule is added after
reapproval but before the next run, the run will still pause. If rule
metadata cannot be read, the run fails with a fixed error code and uses
existing backoff without assuming authorization.

This is a local rule-definition authorization boundary, not a statement
that the imported dataset is fresh or source-verified. Scheduled signal
evaluation still does not send external alerts or authorize business writes.
