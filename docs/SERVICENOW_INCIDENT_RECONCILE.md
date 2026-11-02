# Recover an uncertain ServiceNow → local OWG checkpoint

After an opt-in selected-incident read and private journal commit, the local
OWG append may succeed while its HTTP acknowledgement or subsequent checkpoint
publication is lost. **Do not blindly retry the POST or advance the checkpoint
from journal timestamps alone.** A journal does not prove OWG received events.

The operator-invoked `server.servicenow_incident_reconcile` CLI performs
**offline, authenticated, read-only-to-OWG reconciliation**. It makes no
ServiceNow request, no local OWG append, no source write and no automatic retry.

## Operator recovery

Use the same exact instance, tenant, selected incident IDs, timezone, journal,
checkpoint and local OWG log as the original incremental run. Keep the current
owner-only selected-source grant enabled and use a live dashboard session in
`OWG_DASHBOARD_SESSION` (not an API bearer token). The journal, grant and
checkpoint must be separate private files in owner-only directories.

```sh
python -m server.servicenow_incident_reconcile \
  --instance example.service-now.com --tenant selected-team \
  --incident-id 0123456789abcdef0123456789abcdef \
  --grant-file /private/snow/grant.json --timezone UTC \
  --journal /private/snow/journal.sqlite \
  --checkpoint /private/snow/checkpoint.json \
  --initial-since '2026-10-20 00:00:00' \
  --log-id log_0123456789abcdef \
  --confirm-log-id log_0123456789abcdef --port 8765 \
  --consent-local-journal-read \
  --consent-local-log-read \
  --consent-checkpoint-recovery
```

Use `--initial-since` only when the first-run checkpoint does not yet exist,
and supply the **same** initial provider-local bound as the original run.
For an existing checkpoint it must match the stored bound if supplied.
All three independent consents are mandatory. A private exclusive checkpoint
`.lock` refuses concurrent or stale runs; inspect stale locks before removal.

## What the command actually verifies

The existing authenticated `/v1/event-logs` route must identify the selected
log as belonging to the exact scope-derived source namespace. The command then
GETs `/v1/event-logs/{log_id}/export?format=sourcecsv&case_type=source_case`
over literal IPv4 loopback HTTP with proxies and redirects disabled. It accepts
at most 2 MiB and 4,096 rows, exact CSV schema and unique source keys.

For **every** journal snapshot, the retained local source event must have the
same supplied source-event ID, case ID, activity and UTC timestamp. An ordinary
CSV export does **not** preserve source identity and cannot be used. Extra
retained revisions are allowed only within the already verified namespace and
the bound. Only snapshots in the immutable verified journal export can move the
checkpoint: a concurrent later journal-only write cannot advance it.

The current grant is checked before and after local reads and immediately
before atomic checkpoint publication; the log identity is rechecked before
publication. A CLI cannot atomically fence another actor deleting the log at
that exact instant. Avoid concurrent log deletion. The checkpoint retains its
existing 48-hour overlap policy and exact scope/log/journal binding.

Successful output reports `status: reconciled`, verified snapshot count and
whether the checkpoint advanced. It never prints source IDs, raw provider
data, contact details, credentials or private incident text. Missing or
conflicting events, invalid source CSV, revoked grant, wrong tenant/log,
unsafe paths, missing consent, future timestamps and concurrency return
`not_reconciled` without advancing the checkpoint. If checkpoint publication
has an uncertain filesystem outcome, `checkpoint_commit_unverified` requires
manual inspection of the actual private checkpoint before retrying.

## Evidence and remaining gates

Synthetic integration tests use disposable HTTP backed by OWG's actual
event-log import/append/export engine. They cover lost acknowledgement,
replay, missing/conflicting/oversized exports, consent, revocation, tenant/log
isolation, concurrent journal updates and checkpoint publication failure.
These tests are **not** live ServiceNow/customer acceptance. Exact-head
Ubuntu/macOS/Windows pytest, browser/accessibility, process collaboration,
scale, packaging/release, reviews and current-main compatibility must pass
before merging the draft.

Real provider ACL/timezone validation, complete change history, centrally
enforced revocation, managed credentials, scheduling, retention/deletion and
customer-controlled pilot acceptance remain open. Reconciliation verifies
only that these journal snapshots were present in the local OWG log at the
time of the read; it does not establish complete source history or outcomes.
