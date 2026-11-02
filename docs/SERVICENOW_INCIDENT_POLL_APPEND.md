# ServiceNow selected incidents: finite poll → local OWG append

This is an **operator-invoked, explicitly consented** workflow for up to eight selected ServiceNow incident records. It combines the bounded HTTPS selected-record reader, tenant/selection-bound SQLite revision journal, new private recovery CSV per poll and the authenticated **existing** local OWG event-log append API. Each successful poll appends to the same previously created, source-bound OWG log. It is not a daemon, a general ServiceNow sync, a historical audit feed, or a real-provider acceptance certificate.

## Before running

1. Create an OWG event log using the ServiceNow CSV import with the exact `source_namespace` and `case_namespace` described in `SERVICENOW_OWG_APPEND.md`. Retain its `log_...` identifier. The command **will not create** a log or append to an unrelated namespace.
2. In a private owner-only directory, create an enabled, consented, selection-matching grant JSON as described in `SERVICENOW_INCIDENT_SYNC.md`. The grant must name the exact lowercase `*.service-now.com` instance host, tenant scope and 1–8 selected 32-character incident IDs. Revoking this local marker stops future reads, but it is **not** a substitute for revoking the ServiceNow bearer token or permissions.
3. Obtain an authorized read-only ServiceNow bearer token and the authenticated local OWG dashboard session through approved means. Pass them only as environment variables `OWG_SERVICENOW_TOKEN` and `OWG_DASHBOARD_SESSION`. Do not store either in a file or shell history. Use a dedicated least-privilege source identity.
4. Select an existing private journal path, an owner-only output directory and a new output prefix. All future output paths are checked for collisions before the first provider read.

Example with **placeholders only** (do not use an actual provider without explicit authorization):

```bash
python -m server.servicenow_incident_poll_append \
  --instance example.service-now.com --tenant tenant-label \
  --incident-id 0123456789abcdef0123456789abcdef \
  --grant-file /private/snow/grant.json --timezone UTC \
  --journal /private/snow/journal.sqlite \
  --output-dir /private/snow --output-prefix operator-poll \
  --runs 3 --interval-seconds 60 \
  --log-id log_0123456789abcdef --confirm-log-id log_0123456789abcdef \
  --port 8765 \
  --consent-selected-read --consent-bounded-poll \
  --consent-local-journal-read --consent-local-append
```

`--runs` is 1–8, `--interval-seconds` is 60–1800 and the entire planned wait window is at most 7200 seconds. Each wait checks the grant at most five seconds apart. Each poll performs at most one bounded HTTPS GET for 1–8 explicitly selected incident IDs; the server validates the exact HTTPS host, TLS, redirect prohibition, JSON shape and selected IDs. An optional **fixed** `--since 'YYYY-MM-DD HH:MM:SS'` is an inclusive, operator-declared provider-local lower bound. It is **not** an automatically advanced cursor. The timezone is operator-declared, not provider-verified. No request uses a source-supplied next URL.

## Outcomes and recovery

A successful run produces `operator-poll-001.csv`, `operator-poll-002.csv`, etc. Each is a new private, recoverable cumulative journal export. The existing OWG append route deduplicates deterministic source event IDs; a changed source revision is a new event. The CLI reports counts and `historical_completeness: unknown`, never private incident IDs, descriptions, bearer tokens or dashboard sessions. The source is read-only; the only writes are to the explicitly selected local journal/CSV and local OWG event log.

If a provider read, journal update, output publication, consent check or local append fails, polling stops. If the local POST might have happened, the command returns `append_unverified` and exit code 3: **inspect the local OWG log and journal before choosing a manual recovery action; never blindly retry**. Earlier successful polls remain committed, and the latest CSV may exist even when the local append is unverified. A refusal before a possible append returns exit code 2. Neither result automatically deletes local data or advances a checkpoint. Re-running requires a fresh prefix, explicit consents and current grant; the journal's exact tenant, selected IDs and timezone are rechecked.

## Security and operational limits

The source grant is a **local opt-in marker**, not centrally enforced enterprise revocation or a transactional fence against a concurrently revoked provider token. The local OWG session is checked through the authenticated loopback API before the first source GET and on every poll; the destination log must already exist and have exactly the expected source and case namespaces. HTTP is limited to `127.0.0.1` with proxies and redirects disabled. The journal is capped at 4096 snapshots / 8 MiB; each append payload at 2 MiB. These caps and private directory requirements are inherited from the constituent modules. No source writes, automatic log creation, persistent scheduler, automatic watermark, managed credential vault, journal retention policy or customer rollout is provided. Snapshot reads cannot prove intermediate changes, deleted/omitted incidents or actual business outcomes. Use provider-authorized audit/history extraction and real tenant testing before asserting completeness or compatibility.

Synthetic tests in `tests/test_servicenow_incident_poll_append.py` use a fake ServiceNow transport and a real disposable IPv4 loopback HTTP server backed by OWG's event-log storage engine. They are not real ServiceNow/customer/IdP acceptance. Full native cross-platform CI, browser/accessibility, scale, starter and packaging gates are required before merging.
