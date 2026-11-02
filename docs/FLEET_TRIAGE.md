# Computer rollout triage

The Gateway Employees tab includes a read-only fleet inventory. Search by
employee name/email, team, device ID or reported identity; combine the search
with filters for reported sync problems, unknown recorder or sync status, or
computers not linked to an employee. The summary counts matching computers,
not employees or productivity. Employees without enrolled computers remain in
the existing employee roster below.

The inventory uses the existing administrator-authorized roster response. It
adds no endpoint, collection, upload, grant or remote recording control. Viewer
administrators can use the same read-only filters. Search stays in the browser.
Employee labels come from the roster; unlinked labels remain self-reported.

Refresh status fetches the current roster and invitations. Status filters age
while the page is open: expired problems leave the reported-problems filter and
become unknown. Expiry is latched for each fetched snapshot and bounded by a
monotonic timer; turning the browser clock backward cannot revive expired
claims. A new authenticated refresh can establish fresh status. Missing or stale reports are not evidence that a computer is
stopped or unhealthy. Recorder and sync claims stay separate, as described in
[FLEET_RECORDER_STATUS.md](FLEET_RECORDER_STATUS.md) and
[FLEET_SYNC_HEALTH.md](FLEET_SYNC_HEALTH.md). No evidence-content search is added.
