# First ServiceNow selected-incident journal → OWG event log

Worker E / [#326](https://github.com/KAVentures/openworkgraph/issues/326) Section B / #264 Stage 3 / #370 R3. This is an **operator-invoked, opt-in first-import workflow** using the production local OWG REST API. It closes the manual first CSV-import prerequisite of `SERVICENOW_OWG_APPEND.md`. It does not itself poll ServiceNow, request a credential, infer a complete change history, schedule sync, or perform a provider write.

## Preconditions

1. Run the existing [selected ServiceNow poll/journal](SERVICENOW_INCIDENT_JOURNAL.md) with an authorized **read-only**, current grant for one exact instance, tenant, 1–8 selected sys_ids and declared timezone. Keep the journal and grant in an owner-only directory; the SQLite journal must contain at least one valid event. The grant must remain enabled for the entire import.
2. Start the local OWG dashboard and obtain a current local dashboard session. Supply it through `OWG_DASHBOARD_SESSION` **only**; never paste it into the CLI, logs, repository or provider export.
3. Select a new local event-log name and repeat it exactly. Two separate explicit flags authorize reading the private journal and storing the selected events locally. The command will not silently choose a target, upload externally, or contact ServiceNow.

```sh
python -m server.servicenow_owg_bootstrap \
  --instance sample.service-now.com --tenant team-a \
  --incident-id aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
  --grant-file /private/snow/grant.json --timezone UTC \
  --journal /private/snow/selected.sqlite \
  --log-name 'Reviewed ServiceNow incidents' \
  --confirm-log-name 'Reviewed ServiceNow incidents' \
  --port 8765 \
  --consent-local-journal-read --consent-local-import
```

The command reuses the existing strict journal CSV validation, exact grant and selected-source scope, and deterministic `snow_` source/case namespace. It first reads the authenticated local `/v1/event-logs` inventory and refuses any preexisting log that claims that source namespace. It then makes **one** `POST /v1/event-logs/import` to literal `127.0.0.1` with live `OWG-Session` authorization and `X-OWG-Local-Storage-Confirmed: true`. A second authenticated GET independently verifies the new log ID, source/case namespace, accepted event count and zero rejections. A private exclusive journal-adjacent lock prevents two local bootstrap invocations for that journal. No ambient proxy or redirect is allowed.

A successful counts-only receipt contains the new `log_id` for subsequent invocations of [`servicenow_owg_append`](SERVICENOW_OWG_APPEND.md). Source-event IDs are mapped to the existing transactional dedupe path. Subsequent operator-approved journal append does **not** create a second log and replaying the original journal rows is skipped by source identity.

## One-command selected HTTPS read → journal → first local import

If no journal exists yet, an operator can perform **one** explicitly authorized
ServiceNow selected-incident HTTPS GET, durable journal write and verified first
OWG import in the same invocation. This is a bounded manual workflow, **not**
scheduled synchronization, a provider-side historical feed or a checkpoint.
The grant must already be configured for the exact host, tenant and 1–8
selected incident IDs. Set `OWG_SERVICENOW_TOKEN` to a current read-only
credential in the process environment, and `OWG_DASHBOARD_SESSION` to a
current local dashboard session. Neither credential is printed or saved by
this command.

```sh
python -m server.servicenow_owg_bootstrap \
  --instance sample.service-now.com --tenant team-a \
  --incident-id aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
  --grant-file /private/snow/grant.json --timezone UTC \
  --journal /private/snow/selected.sqlite \
  --log-name 'Reviewed ServiceNow incidents' \
  --confirm-log-name 'Reviewed ServiceNow incidents' \
  --port 8765 \
  --read-selected --consent-selected-read \
  --consent-local-journal-read --consent-local-import
```

Three separate opt-ins authorize the selected provider GET, local journal
read and local event-log storage. `--since 'YYYY-MM-DD HH:MM:SS'` is an
optional **operator-selected provider-local lower bound**, not a saved or
automatically advanced checkpoint. `--read-selected` without
`--consent-selected-read`, or a source-read consent without its mode, is
refused. The original journal-only bootstrap remains unchanged and requires
no provider credential.

Before any provider request, OWG validates the private journal's existing
scope (if any), rechecks the exact selected grant and authenticates to the
local OWG log inventory. An already-imported source namespace, wrong
journal scope, revoked grant or invalid local session **prevents the source
GET**. The production selected reader enforces the same exact host, single
bounded response, no redirects, no truncated/duplicate/foreign records and
explicit IANA timezone conversion as the separate ServiceNow journal CLI.
Only selected hashed case/event IDs, state and UTC timestamps enter the
owner-private SQLite journal; unrelated incident text is discarded. The
journal transaction deduplicates source revision IDs before any OWG POST.

After journaling, the existing strict CSV validator and authenticated OWG
import/readback path are reused. The counts-only receipt additionally
reports `provider_read_performed`, `new_journal_snapshots` and
`replayed_journal_snapshots`. No source writes, background requests or
automatic retries occur. A provider/read failure before the OWG POST leaves
no new OWG log; a successful journal commit can survive a later import
failure. An uncertain POST **must not** be retried blindly: inspect OWG's
log inventory and the durable journal first, then use the existing
reviewed append path when appropriate.

This is production-path integration through the real selected-source
reader, SQLite journal and authenticated FastAPI/DuckDB import, tested with
disposable provider-shaped transport. It is **not** evidence of acceptance
against an authorized customer-controlled ServiceNow instance. Managed
credential rotation, historical completeness, deleted-record handling,
scheduled polling and retention approvals remain separate gaps.

## Failure and recovery

A refusal **before** POST creates no local log. After the import request is attempted, any transport failure, malformed receipt, revoked grant, missing/changed readback or concurrent same-scope log is reported as `status=unverified`, `possible_local_import=true`. The import **may have committed**. Do not automatically retry. Open OWG's Event logs view, inspect the current log and its namespace/counts, resolve any duplicate-source logs, and only then continue with an explicitly reviewed append. A provider-side grant can also be revoked independently; the local grant is not a global atomic revocation fence.

The journal, source-selected hashes and OWG event log remain local business metadata and are subject to the organization's retention, access-control, backup and deletion policy. This does not assert source completeness, provider tenant proof, business outcomes or employee performance. Real authorized ServiceNow customer sandbox acceptance is still outstanding. The committed integration suite uses the real SQLite journal, actual FastAPI/DuckDB event-log routes and disposable loopback TCP, including deduplication, revoked grant, invalid journal, wrong scope, duplicate-source, authentication and uncertain-readback refusal. **Native Ubuntu/macOS/Windows, browser, process collaboration, scale, accessibility, starter and packaging/release CI must all pass on the exact PR head before merge.**
