# ServiceNow: finite incremental checkpoint polling into OWG

**Scope:** [#326 Section B](https://github.com/KAVentures/openworkgraph/issues/326), [#264 Stage 3](https://github.com/KAVentures/openworkgraph/issues/264). This operator-invoked command combines the existing bounded selected-incident HTTPS reader, private SQLite journal, authenticated local OWG append and durable incremental checkpoint **after each successful poll**. It does not run as a background daemon, discover incidents, extract all historical transitions or perform ServiceNow business writes. A real authorized tenant test has **not** been performed.

## Operator setup and consent

Follow [ServiceNow checkpoint setup](SERVICENOW_INCIDENT_CHECKPOINT.md) and [local append](SERVICENOW_OWG_APPEND.md). Prepare an owner-only private directory, a matching enabled grant for the exact host/tenant and 1–8 selected literal incident IDs, a previously created namespace-matched local OWG CSV event log, a current OWG dashboard session and an authorized **read-only** ServiceNow bearer token. Set credentials in the current environment only; do not put them in flags, source control or logs.

Use a declared instance IANA timezone. The first run requires an inclusive provider-local lower bound. Example with placeholders, never a real credential:

~~~sh
export OWG_SERVICENOW_TOKEN='<approved-read-only-token>'
export OWG_DASHBOARD_SESSION='<current-local-session>'
python -m server.servicenow_incident_checkpoint_poll \
  --instance sample.service-now.com --tenant approved-tenant \
  --incident-id 0123456789abcdef0123456789abcdef \
  --grant-file /private/owg/snow-grant.json --timezone UTC \
  --journal /private/owg/snow-journal.sqlite \
  --checkpoint /private/owg/snow-checkpoint.json \
  --output-dir /private/owg --output-prefix snow-shift-001 \
  --runs 3 --interval-seconds 300 \
  --initial-since '2026-10-20 00:00:00' \
  --log-id EXISTING_OWG_LOG_ID --confirm-log-id EXISTING_OWG_LOG_ID \
  --consent-selected-read --consent-bounded-poll \
  --consent-local-journal-read --consent-local-append \
  --consent-incremental-checkpoint
~~~

Each invocation allows **1–8 polls**, an interval of **60–1,800 seconds**, and at most **two hours** between the first and last scheduled polls. All five consents are mandatory. Every output is prevalidated to be a *new* private CSV; the names follow prefix-001.csv, prefix-002.csv, etc. No existing file is overwritten. Before the first ServiceNow GET, the command checks the selected scope, grant, private paths, checkpoint binding and current authenticated **loopback-only** OWG log namespace. A checkpoint from a different tenant, selection, timezone, journal or log refuses the run.

The first poll reads from the initial lower bound; each subsequent successful poll uses the latest **durably committed** checkpoint's 48-hour overlapping lower bound. Replayed source revision IDs are deduplicated by the private journal and OWG's transactional append. Every successful poll publishes a separate recovery CSV, appends through the existing authenticated local OWG endpoint, verifies the checkpoint file and only then proceeds. Grant status is rechecked every five seconds during waits and at each component boundary. The command does not retry an uncertain local POST or checkpoint publication.

## Resume and recovery

To resume after a fully successful invocation, use a **different output prefix**, omit the initial-since option, and provide the same host, tenant, selected IDs, timezone, journal, checkpoint and OWG log. If a poll is refused, previous completed polls and checkpoint commits remain durable. Do not reuse an existing output prefix. A refused/uncertain poll terminates the sequence; its receipt reports the count of *fully completed* polls and whether a local append may have occurred.

If append_or_checkpoint_unverified is returned, **do not rerun automatically**. Inspect the actual OWG log and journal; the [read-only reconciliation command](SERVICENOW_INCIDENT_RECONCILE.md) can recover a checkpoint after verifying every journaled source event against the retained local OWG log. An uncertain checkpoint may be partially published even when the receipt says unverified. Never remove a stale lock without investigating an active process.

## Limits and security

This is **finite operator scheduling**, not a production scheduler or centralized revocation service. Local grant-file checks cannot atomically fence in-flight provider requests or external token revocation; revoke provider credentials and OWG sessions separately. Selected Table API snapshots are not a complete change feed: intermediate updates, older late arrivals, deletions, ACL-filtered records, unverified source timezone and missing history remain unknown. The journal caps at 4,096 snapshots / 8 MiB and is **not automatically pruned**. Configure retention/deletion separately; checkpoint progression is not a verified business outcome.

The test module tests/test_servicenow_incident_checkpoint_poll.py uses a fake provider and a disposable **actual local HTTP server backed by OWG's event-log engine** to cover successive changes, deduplication, resume, revocation during waits, uncertain append, scope/output preflight and forged child receipts. These are synthetic integration tests, not real ServiceNow or customer acceptance. Native Ubuntu/macOS/Windows pytest, browser, process collaboration, accessibility, scale, starter and packaging/release checks remain mandatory before merging and rollout.
