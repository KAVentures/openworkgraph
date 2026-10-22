# Company fleet recorder status

The Gateway admin console shows content-free recorder status beside each
linked or unlinked enrolled computer. The overview also counts fresh recorder
reports at refresh time. Last-evidence timestamps and event totals remain
separate: an idle, paused or privacy-filtered employee may produce no evidence
while their deployment continues to report normally.

This uses the existing authenticated device heartbeat endpoint and table. It
adds no collector, work-content upload, source connector probe or remote
recording controls. Observation, sharing policy and AI access are unchanged.
Existing administrator authentication and roles protect the roster endpoint;
ordinary device and integration tokens cannot read this inventory. Responses
are marked `Cache-Control: no-store`. Employees' existing self-service device
view receives only their own computer statuses through its existing filtering.

The query matches organization, current actor and device. A heartbeat older
than the active credential's creation or the identity link is excluded. New
credentials and reassigned devices must report again; a previous enrollment's
heartbeat cannot establish current status. Revoked computers remain excluded
by the existing active-token filter. One comparison time is used for the
returned device set.

A valid device report stays fresh for at most 60 seconds. Recording also
requires a recording capture-policy claim and a collector signal no older than
45 seconds, including time elapsed since the server received the report.
Otherwise the UI asks for a collector refresh. Fresh paused, stopped and
missing-collector reports are displayed explicitly. Stale, absent, invalid or
future reports mean unknown. They do not mean that recording stopped.

The console displays when the device reported and expires status badges while
the page is open. Reopen the Employees tab to retrieve new server statuses.
The overview's count describes its refresh time. These are authenticated device
claims, not independent verification of the operating system, employee activity,
evidence synchronization, business connector health or business outcomes.

Verification covers identity and tenant matching, credential replacement,
revocation, invalid/future/stale timestamps, collector-signal aging, existing
admin boundaries, no-store responses and the actual browser flow from device
reports through the production roster query. SQLite and disposable PostgreSQL
exercise the same join; synthetic browser fixtures enroll through the real API
and generate no work evidence.

The hosted MCP recorder summary uses the same timing checks and preserves its
existing response fields. It selects only reports from active device credentials
for the authenticated organization/actor/device, excluding reports older than
those credentials. A revoked or replaced credential cannot establish current
recorder status. An ineligible newer report does not bury another eligible
computer's report. Future timestamps are unknown, and the hosted collector
signal flag includes elapsed time since receipt in its 45-second window.

Heartbeats are bound internally to the submitting device credential ID. Both the
admin join and hosted lookup require that exact credential to remain active;
matching timestamps alone cannot establish a replacement credential's status.
The write also checks that the submitting credential is still active, preventing
an old in-flight request from replacing a new credential's report. Actual bearer
tokens are never retained in heartbeat rows or returned by these summaries.

Startup adds the binding column to legacy SQLite/PostgreSQL tables without
deleting existing metadata. Legacy unbound rows remain unknown until the next
normal authenticated heartbeat. Existing connectors need no protocol change.
Schema initialization is repeatable, and SQLite serializes its migration check
with the update. Tests include equal enrollment/report timestamps, overlapping
revocation and writes, and repeat startup over a populated legacy schema.
