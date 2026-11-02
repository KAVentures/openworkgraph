# ServiceNow selected incident journal (operator-invoked, disabled by default)

This is an additive, durable multi-run **local** snapshot collector for the
[opt-in selected incident HTTPS reader](SERVICENOW_INCIDENT_SYNC.md). It is
part of #326 Section B / #264 Stage 3, not a ServiceNow change-history feed,
a scheduled job, an enterprise provisioning service, or a source write.

## End-to-end use

Create a **private owner-only directory** for the journal, grant and exports
(chmod 700 on POSIX). Prepare the same 0600 grant JSON described in
[SERVICENOW_INCIDENT_SYNC.md](SERVICENOW_INCIDENT_SYNC.md) for one exact
ServiceNow instance, local tenant and 1–8 incident sys_ids. A tenant admin
must independently confirm ServiceNow ACLs and the raw timestamp timezone.
Supply the read-only bearer credential through OWG_SERVICENOW_TOKEN in the
process environment; never put it in a CLI argument, CSV or journal.

Run an explicitly consented selected current-snapshot read:

~~~sh
python -m server.servicenow_incident_journal \
  --instance example.service-now.com --tenant selected-team \
  --incident-id 0123456789abcdef0123456789abcdef \
  --grant-file /private/snow/grant.json --timezone UTC \
  --journal /private/snow/snapshots.sqlite \
  --output /private/snow/export-001.csv --consent-selected-read
~~~

On later operator-initiated runs, use the **same** host, tenant, exact selected
IDs, timezone and journal, but a **new output filename**. Optionally pass an
explicit inclusive source-local --since "YYYY-MM-DD HH:MM:SS" to bound the
GET. No timestamp is ever auto-advanced; a snapshot API cannot prove that
intervening revisions were observed. A returned empty filtered page never
means a selected incident was deleted.

The CLI performs at most one bounded TLS GET per run using the existing
selected-incident reader: no proxies, redirects, provider-supplied next links,
arbitrary tables, raw descriptions or caller data. The local matching grant
is checked before the request, after the response, during journal commit and
before CSV publication. Remove or disable the grant to block future runs.
**A local file cannot atomically fence concurrent revocation**, and provider
token/ACL revocation is separate.

The journal is an owner-only SQLite file in a private directory. It binds
exact host, local tenant, selected IDs and declared timezone via a digest.
Only hashed case/revision keys, reported state codes and declared-timezone
UTC instants are stored, not raw incident IDs, private descriptions, token,
employee identities or provider JSON. At most 4,096 snapshots / 8 MiB are
accepted. An exact revision replay is skipped; the same revision key with
different contents is refused transactionally. Separate processes serialize
updates through SQLite transactions; a private file, symlink and scope checks
prevent accidental cross-tenant mixing. The journal is **not** a trusted
provider receipt or independent verification of tenant authority.

Every successful invocation exports the entire bounded journal as a **new**
0600 CSV with case_id, activity, timestamp, source_event_id for
OWG **Event logs → Import CSV**, followed by user-triggered append/dedupe.
The CLI never appends to an OWG log, uploads, grants permissions, observes
employees, profiles performance, runs an agent or writes to ServiceNow.

## Crash/retry and offline recovery

The SQLite transaction commits **before** exclusive CSV publication. If a
new output filename cannot be published (for example, it already exists),
the journal may still contain the new snapshots. Do not delete it or retry
against the provider merely to recover a CSV. Instead run:

~~~sh
python -m server.servicenow_incident_journal \
  --instance example.service-now.com --tenant selected-team \
  --incident-id 0123456789abcdef0123456789abcdef \
  --grant-file /private/snow/grant.json --timezone UTC \
  --journal /private/snow/snapshots.sqlite \
  --output /private/snow/recovered.csv \
  --export-only --consent-local-export
~~~

This mode makes **no provider request** and needs no bearer token, but still
requires the matching current local grant and separate explicit local export
consent. It refuses a missing journal, mismatched selection, revoked grant,
symlink, public journal, existing output path or invalid database. If a
process crashes while SQLite is committing, SQLite's rollback journal
recovers an all-or-nothing transaction on next open. Do not manually remove
SQLite rollback files or share the journal directory with other OS users.

The output JSON reports new/replayed/stored snapshot counts and fixed
uncertainty flags. It does not contain source IDs, source URLs, token,
provider response text, or private incident descriptions. Both the journal
and exported CSV still contain potentially sensitive business metadata;
apply your organization's retention, backup encryption, access and deletion
policy. This feature does not implement automatic retention or deletion.

## Validation and boundaries

Synthetic CLI tests cover real code paths with a fake HTTP transport:
two-run replay and new revisions, source-filtered empty reads, export-only
recovery after failed CSV publication, revocation, selection/tenant isolation,
conflicting revisions, symlinks and permissions. **They are not a live
ServiceNow tenant test**. Exact-head native Ubuntu/macOS/Windows pytest,
browser, collaboration, scale, accessibility and packaged-release checks
must pass before merging the draft.

Remaining rollout gaps: independently authorized disposable ServiceNow
sandbox acceptance, provider ACL and timezone verification, central
authorization with atomic revocation, scheduled/retry/backoff orchestration,
source-complete change history, centrally managed encrypted credentials,
retention policy enforcement and automatic OWG workspace append. Do not
claim historical completeness, verified outcomes or customer acceptance
from snapshot counters or synthetic tests.
