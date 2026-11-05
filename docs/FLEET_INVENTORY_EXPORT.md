# Filtered fleet inventory CSV

From the Gateway Employees tab, **Download shown computers as CSV** exports
only the enrolled computers currently matching the fleet search and status
filter. Empty selections cannot be downloaded. The file records shown and total
enrolled computer counts, selected status filter, roster-fetch time and
export-preparation time, plus each computer's roster identity, teams, device ID
and recorder/sync claims. Search terms are not retained in the file.

Queue and rejected-event counts are included only for a fresh sync report.
Freshness is re-evaluated at click using the snapshot's monotonic expiry latch;
expired claims remain unknown even after a browser clock rollback. Exported
claims are a point-in-time snapshot, not live monitoring. Roster fetch and
export times are client wall-clock labels, not an authoritative server clock.

CSV cells are quoted and spreadsheet formula prefixes are neutralized, including
prefixes following whitespace. Team labels use a JSON array within the CSV cell
to preserve literal commas and separators. The file excludes work evidence,
event totals, invitation codes, credentials and tokens. It still contains
employee identities and device identifiers: review before sharing. Downloads are
browser-local and send nothing to another service. Existing administrator read
authority applies, including viewer access; no new grants or controls are added.
A download uses only a roster fetched for the current signed-in session and
organization. Signing out clears the local fleet snapshot, and late responses
from an earlier session or superseded refresh cannot restore it.
