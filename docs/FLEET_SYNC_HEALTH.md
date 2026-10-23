# Company fleet sync health

The Gateway admin console shows, beside each enrolled computer's recorder
status, whether that computer reports its evidence synchronization as current.
This answers the rollout question the recorder status cannot: is captured
evidence actually reaching the Gateway, or is it waiting, failing or rejected?

## What a device reports

Every 20 seconds, alongside its recorder heartbeat, the connector posts
`/v1/device/sync-health` with counts only:

| Field | Meaning |
| --- | --- |
| `sync_state` | The connector's own state: connected, syncing, error, not_started, local_only or stopped |
| `unprocessed_local_events` | Local rows after the sync cursor that have not been sent, skipped or quarantined yet |
| `quarantined_events` | Rows the Gateway rejected as terminally invalid |
| `last_success_age_seconds` | Seconds since the last fully acknowledged batch, if any |
| `sync_error_present` | Whether the last attempt recorded an error; the error text is never sent |

No event IDs, titles, error messages or other evidence content leave the
device. A paused computer sends nothing, exactly like its recorder heartbeat.
An older Gateway without the route is ignored by the connector.

## How the Gateway uses it

Reports are bound to the submitting device credential and written only while
that credential is active, using the same revocation fence as recorder
heartbeats. The roster joins only reports from the current credential, so a
replaced or revoked enrollment cannot speak for the new one. A report is
current for 120 seconds; elapsed time since receipt also ages the device's
last-success claim.

Statuses: `in_sync_reported`, `sync_error_reported`,
`quarantined_events_reported`, `backlog_reported` (500 or more waiting, or any
waiting with no success in 15 minutes) and `sync_not_running_reported`. All
problems are listed in `problems`; missing, stale, invalid, future or
pre-enrollment reports are `unknown`. The overview counts devices with any
reported problem.

These are authenticated device claims. They do not prove that every captured
event was shared, and the Gateway's own last-received evidence time and event
count stay separate. No remote controls are added.

Verification: `tests/test_fleet_sync_health.py` (statuses, credential binding
and fencing on SQLite and PostgreSQL, the authenticated route and the
connector's counts-only payload) and `tests/browser/fleet_sync_health.cjs`
(real admin console badges, counts, overview total and independent expiry).
