# Local signal-review inbox (roadmap #370 R10 / M6)

The optional event-log signal inbox records explicit review of a **specific
evaluated signal state** on the user's own computer. It does not fetch business
records, enable background work, send notifications, grant execution authority
or modify an alert rule.

- Evaluate configured signals using the existing dashboard-only
  POST /v1/event-logs/{log_id}/signals/evaluate endpoint.
- GET /v1/event-logs/{log_id}/signal-inbox reports not_evaluated, cleared,
  unreviewed or acknowledged. Acknowledgment has no effect on actual rule
  evaluation. The inbox returns no raw case identities or source data.
- POST /v1/event-logs/{log_id}/signal-inbox/{rule_id}/acknowledge with exact JSON
  {"state_fingerprint": "<current 64-character lowercase digest>"} records an
  explicit review of a presently triggered state. Stale, false, missing and
  malformed states are rejected. The token is state-based so repeated
  unchanged evaluations do not erase an acknowledgment.
- POST to the same route ending /reopen with the same payload removes the
  acknowledgment, returning the current rule to unreviewed.
- Any event-log append (including a duplicate-only replay) makes the prior
  evaluation stale. The inbox then withholds its fingerprint and refuses new
  acknowledgements until a user explicitly reevaluates the rule.
- The review expires when the next evaluated state changes. Clearing a signal
  removes its review, so a later recurrence needs new acknowledgment even if
  the triggering state is identical.
- Reviews are deleted when the rule or log is deleted. Existing log retention,
  local-dashboard auth and no-action boundaries remain unchanged.

This is a first slice. Remaining R10 acceptance: explicit opt-in schedules,
deduplicated alert event lifecycle, quiet hours,
auditable recipient authorization, safe retry, source freshness/revocation,
real-browser UX and company deployment. No scheduled task or external
notification is enabled by this patch.


## Time-bounded local snooze

Triggered unreviewed results offer **Snooze 1 hour**, **Snooze 1 day** and
**Resume review**. The inbox shows the UTC expiration. On the next refresh at
or after expiration, the exact result returns to review; no background timer or
notification runs. The API POST `signal-inbox/{rule_id}/snooze` accepts only
`state_fingerprint` and integer `minutes` (1–43200, or 0 to resume).
Repeated requests while the same snooze is active preserve the original expiry.
To change its duration, resume and snooze again.

Snooze is separate from acknowledgement. Acknowledge/reopen removes the snooze;
acknowledged results must be reopened before snoozing. A changed evaluation
clears it, and a changed dataset makes the result stale even during a snooze.
Deleting the rule or log removes its snooze. It grants no actions, delivery,
source access or execution permissions. The local computer clock determines
expiry; this is not a guaranteed reminder or enterprise notification service.
