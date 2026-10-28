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
- The review expires when the next evaluated state changes. Clearing a signal
  removes its review, so a later recurrence needs new acknowledgment even if
  the triggering state is identical.
- Reviews are deleted when the rule or log is deleted. Existing log retention,
  local-dashboard auth and no-action boundaries remain unchanged.

This is a first slice. Remaining R10 acceptance: explicit opt-in schedules,
deduplicated alert event lifecycle, quiet hours, snooze and expiration,
auditable recipient authorization, safe retry, source freshness/revocation,
real-browser UX and company deployment. No scheduled task or external
notification is enabled by this patch.
