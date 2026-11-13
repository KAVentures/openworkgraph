# Download a domain-pack report

After installing a pack and choosing **Run pack report** in the local event-log
workspace, choose **Download report JSON**. The browser saves the report already
shown; downloading performs no additional source request, upload or business action.

The envelope uses `openworkgraph.domain-pack-report-snapshot.v1` with a browser
`generated_at` timestamp and the complete `report`. That report retains the exact
pack version, installed definition digest, saved mapping, parameters, supplied-log
metadata, metric coverage, rule results, examples, assumptions, interpretation
limits and descriptive-only flags. The display identifies the saved parameters.

The file includes supplied case/object examples and mapped labels. The UI says so
before download. Save or share it only where authorized. It is a local export,
not an AI-access change or permission to publish evidence.

Editing a role mapping or parameter clears results, disables downloading and
requires installing the changed inputs before running again. Starting another
report, reinstalling, uninstalling, switching packs or reloading setup also
invalidates the old result. A late response cannot restore a report invalidated
by a later input change. Failed report requests leave downloading unavailable.

An exported file is a historical snapshot, not proof that the log or installation
is still current. The definition digest identifies resolved pack definitions,
not a hash of source events or the exported file. The browser timestamp is not
an independently verified source receipt. Re-run to inspect current retained data;
missing source history and policy/outcome verification remain explicit limitations.

Acceptance uses an owned synthetic O2C log in Chromium, checks downloaded bytes
against exact mapping/version/digest/parameters, changes targets, holds an HTTP
response while inputs change, reinstalls and uninstalls, and checks axe/mobile.
Existing owner-session report access and local data consent stay unchanged.
