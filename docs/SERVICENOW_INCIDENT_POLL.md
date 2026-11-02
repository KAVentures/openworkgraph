# ServiceNow finite selected-incident polling (operator opt-in)

This is a working **bounded, operator-initiated** polling command for
[#326 Section B](https://github.com/KAVentures/openworkgraph/issues/326)
and [#264 Stage 3](https://github.com/KAVentures/openworkgraph/issues/264).
It composes the existing selected-incident HTTPS reader and durable SQLite
journal; it is **not** a daemon, a cron installation, a complete change-history
feed, a ServiceNow webhook, an automatic OWG event-log append or a real-tenant
acceptance claim.

## Use

First follow [SERVICENOW_INCIDENT_JOURNAL.md](SERVICENOW_INCIDENT_JOURNAL.md)
to create a private owner-only directory (POSIX mode 0700), an enabled 0600
host/tenant/selected-ID-scoped grant and a read-only ServiceNow bearer token
in the `OWG_SERVICENOW_TOKEN` environment variable. The operator must verify
provider ACLs, authorized incident selection and source timezone independently.
Nothing runs unless this command is explicitly invoked.

```sh
python -m server.servicenow_incident_poll \
  --instance example.service-now.com --tenant selected-team \
  --incident-id 0123456789abcdef0123456789abcdef \
  --grant-file /private/snow/grant.json --timezone UTC \
  --journal /private/snow/snapshots.sqlite \
  --output-dir /private/snow --output-prefix morning-20261101 \
  --runs 3 --interval-seconds 300 \
  --consent-selected-read --consent-bounded-poll
```

This performs **at most three** bounded selected-incident HTTPS GETs, spaced
five minutes apart, and creates three new owner-only CSV exports:
`morning-20261101-001.csv`, `-002.csv`, `-003.csv`. The same private,
scope-bound journal persists snapshots across invocations, deduplicating exact
revisions and rejecting conflicting replays. The outputs are for explicit
**OWG Event logs → Import CSV** and user-triggered append/dedupe, not an
automatic ingestion. All output names must be unused before any source GET;
publication is exclusive and refuses races/overwrites.

Poll count is **1–8**, interval **60–1800 seconds**, total planned wait at
most **7200 seconds**. This is a finite run, not a recurring scheduler or
indefinite monitoring. The grant is rechecked before each GET and every five
seconds during the wait. Removing/disabling the grant stops further polls,
but a local grant is not an atomic provider-side revocation fence: separately
revoke the provider token/ACL. The command stops on the first failed read,
revoked grant, invalid scope or publication error. Earlier successful CSVs
and committed journal snapshots remain; use the existing journal's separate
consented `--export-only` recovery for interrupted publication.

An optional `--since "YYYY-MM-DD HH:MM:SS"` is an **explicit fixed**
provider-local inclusive lower bound reused on every poll, not a calculated
cursor. Do not automatically advance it based on returned snapshots:
ServiceNow's current-state incident Table API can skip intermediate state
changes. A successful response is not evidence of complete history, an
incident deletion, business outcome, provider tenant identity or actual
customer acceptance. No source writes, automatic OWG append, employee
capture, permissions, cloud upload or AI execution are performed.

The CLI emits one minimized JSON summary with completed polls, new/replayed
snapshot counts, journal size and explicit unknown-completeness flags. If a
later poll fails it emits a generic refusal with the number of *previously*
completed polls. It never echoes bearer credentials, incident IDs, source
URLs, source JSON, employee names or descriptions. The CSV and journal
remain potentially sensitive business metadata subject to the operator's
retention, encryption, backup and deletion policy.

## Acceptance and remaining gaps

Synthetic tests use the actual CLI and fake HTTPS responses to exercise
two successive reads with a new revision, identical replay, grant revocation
during the wait, provider refusal on the second read, output collision,
bounds, no consent and cross-tenant journal reuse. These do **not** establish
real ServiceNow provider compatibility or authorize a production call.
Native Ubuntu/macOS/Windows pytest, browser, accessibility, scale, starter,
packaging and release checks must pass at the exact PR head before merge.

Remaining rollout gaps: approved disposable ServiceNow sandbox acceptance;
verified provider ACL/timezone; centrally atomic grant revocation and managed
credentials; audited enterprise scheduler/retry/backoff; source-complete
change-history ingestion; explicit journal retention enforcement; and
authorized OWG workspace append. Do not install an unattended scheduler
without separate operator/customer approval and security review.
