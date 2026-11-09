# Retained Jira schedule status

Retained Jira profile responses now include `scheduled_runs_enabled`. It is true
when the existing local scheduler has an enabled `retained_jira_profile` schedule
for that exact profile. `manual_runs_only` is its inverse. The status inventory
reports whether any currently listed profile has such a schedule. Unscheduled
profiles and an empty inventory preserve the manual-only default.

Pause/delete removes enabled scheduling from this projection; resume restores it.
An event-log signal schedule or schedule for another/deleted profile does not
change a profile's flag. This corrects the previous unconditional manual-only
claim after local scheduling was introduced.

These fields describe local configuration, not whether a job is currently due or
will succeed. Quiet hours, recording pause, disabled profiles, grants, credential
checks, retry/backoff and provider availability still apply independently.
Scheduling never verifies credential health, source completeness, business
outcomes or alert eligibility. Existing unknown/unverified flags remain intact.

Reading status only reads local schedule configuration. It does not create or
modify a schedule, run a job, contact Jira, refresh credentials, broaden retained
scope or enable recording. Existing dashboard authorization remains unchanged.

Tests create, pause, resume and delete actual isolated local schedules, check
exact profile/kind isolation and deleted-profile behavior, and forbid job/provider
execution while confirming schedule files remain unchanged.
