# ServiceNow selected-incident read → local OWG append (operator invoked)

Issue: [#326 Section B](https://github.com/KAVentures/openworkgraph/issues/326), [#264 Stage 3](https://github.com/KAVentures/openworkgraph/issues/264). This composes the existing bounded ServiceNow HTTPS reader, durable journal and authenticated local OWG append. It is **not** an unattended connector, complete change history, customer validation or a permission to make business writes.

## What an operator can do

After setting up the owner-only matching selected-incident grant, a previously created OWG event log whose sole source/case namespace is the same exact ServiceNow host/tenant/selected-ID/timezone scope, and a current local dashboard session, run:

\`\`\`sh
# Values are illustrative. Use your own authorized ServiceNow instance,
# selected incident sys_id(s), owner-only grant and private local paths.
export OWG_SERVICENOW_TOKEN='<authorized-provider-bearer-token>'
export OWG_DASHBOARD_SESSION='<current-local-OWG-session>'
python -m server.servicenow_incident_run \
  --instance example.service-now.com --tenant team-a \
  --incident-id aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
  --grant-file /private/owg-snow/grant.json --timezone Europe/Stockholm \
  --journal /private/owg-snow/snapshots.sqlite \
  --output /private/owg-snow/recovery-001.csv \
  --log-id log_0123456789abcdef --confirm-log-id log_0123456789abcdef \
  --port 8765 \
  --consent-selected-read --consent-local-journal-read --consent-local-append
\`\`\`

Do not paste tokens into CLI flags or logs. The two tokens are read only from environment variables. Do not run against a real tenant without explicit authority. Use a **new** output CSV path for each invocation; it is a private recovery artifact and is never overwritten. The operator can optionally provide a fixed, inclusive provider-local \`--since "YYYY-MM-DD HH:MM:SS"\` lower bound. It is not automatically advanced.

The command checks all three explicit consents, matching owner-only grant, private journal/output paths, scope, exact repeated destination log ID, dashboard session and **read-only authenticated loopback destination namespace before contacting ServiceNow**. It then performs one bounded HTTPS GET for 1–8 literal selected incidents, normalizes minimized current-state snapshots, transactionally deduplicates revisions in the scope-bound SQLite journal, writes a new private recovery CSV, and sends the bounded journal through OWG's existing authenticated local event-log append route. No new log is silently created. No source writes occur. Exact replayed source event IDs are deduplicated by the OWG append engine; conflicting revisions are refused by its existing transaction.

On success the JSON receipt reports journal/new/replayed snapshots, added/duplicate/rejected OWG events and unknown historical completeness. An unsuccessful provider read does not trigger a local append. If a local POST was attempted but the response or grant becomes uncertain, the receipt says \`append_unverified\` with \`possible_local_append: true\`; **inspect the existing OWG append history before any manual retry**. The command never retries a POST automatically. A failed CSV publication after a journal commit may be recovered using the journal's separately consented export-only command. Source record text, incident IDs, tokens and local session are not included in receipts.

## Boundaries and rollout gaps

- This is a **one-shot operator command**, not scheduling, a background worker, provider-wide discovery or an incremental change feed. The source Table API exposes current snapshots, not all intermediate transitions; missed changes and source timezone remain unverified.
- The ServiceNow host is pinned to a literal allowed \`*.service-now.com\` name, with TLS hostname verification, no redirects/proxies and bounded response/time. The local append uses only literal IPv4 loopback with no proxy, DNS or redirect.
- The grant is rechecked at component boundaries and before source reads/local append; a local file cannot atomically fence external IdP/provider revocation or a concurrent final race. Revoke the provider bearer token and OWG session separately.
- Journal retention is bounded by event count and bytes but **does not implement automatic time-based erasure**. Operators must apply their approved retention/deletion policy.
- No real ServiceNow sandbox/customer validation, Entra/Okta certification, source-complete history, centralized credential vault, production scheduling or verified business outcome is claimed.
- Synthetic tests use a fake ServiceNow provider and a real disposable loopback HTTP server invoking OWG's event-log storage engine; they are not provider acceptance tests.

For independent operation and recovery, see [selected-source journal](SERVICENOW_INCIDENT_JOURNAL.md), [finite polling](SERVICENOW_INCIDENT_POLL.md) and [local append](SERVICENOW_OWG_APPEND.md).
