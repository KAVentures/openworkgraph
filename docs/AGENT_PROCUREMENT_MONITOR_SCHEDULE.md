# Scheduled monitoring deadline review

Two explicit opt-ins are required. No schedule is created by default.

1. For a retained journal, use router-journal-schedule with the existing
   dashboard-only JSON-text envelope and exact workflow/revision query
   parameters. Document: monitor_id, expected_head_sha256, enabled (boolean),
   confirmed=true. Enabling requires the current process revision and a
   retained verified plan. Disabling remains allowed on a historical
   revision. Consent changes are hash-chained and never implied by an
   earlier manual or producer checkpoint. A stale head is rejected.
2. In Process catalog, open Schedule local workforce deadline reviews,
   load status, and explicitly enable the local job. Alternatively create
   the existing scheduler job kind procurement_monitor_deadlines with
   target_id=local, interval_minutes=15–10080, confirm_scheduled_runs=true.
   Existing scheduler pause, resume, removal, quiet hours, bounded history,
   serial execution and failure backoff apply.

Every run examines the bounded retained local journal set. A journal must
still be opted in and on the current process revision inside the SQLite
write transaction. The job computes frozen window start, close and
reporting-deadline boundaries crossed since the retained cutoff. If none
were crossed, it does not append an entry. Otherwise it advances the cutoff
to the local server clock, recomputes the original monitoring checks without
new observations, and retains a scheduled_checkpoint with all crossed
boundaries, overdue windows and rollback-review signals. Existing sticky
incidents, finance declarations, and producer attribution survive.

Concurrent manual/producer writes and consent changes are serialized.
Repeated ticks or process restarts do not create duplicate deadline
checkpoints because the retained cutoff is authoritative. A corrupt or
capacity-limited journal fails independently; the scheduler reports a
fixed failure code and applies its existing backoff. Historical journals
are skipped, not silently deleted. Removing a journal removes its
journal-specific consent; the global schedule can remain for other journals.

This is local clock-based evaluation, not a source polling service or a
remote scheduler. It runs only while the existing OWG scheduler is active.
It reads already retained declarations, so it does not require active
ambient recording, and does not ingest new events. It sends no external
notification, performs no business operation and has no rollback authority.
The overview and retained journal expose the resulting incident/deadline
state. No timing guarantee is made while the application is closed.

While scheduled consent is enabled, the final audit entry is reserved for
explicit withdrawal. Capacity exhaustion cannot consume that reserved entry.
An already deleted process design never receives a scheduled update; its
retained journals remain inspectable and explicitly removable by exact scope
through the dashboard-session API.

Audit capacity is reserved jointly for every outstanding producer revocation
and scheduled consent withdrawal; normal checkpoints cannot use it.
