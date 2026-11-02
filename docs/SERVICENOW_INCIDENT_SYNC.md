# ServiceNow: operator-initiated selected-incident HTTPS → OWG event-log CSV

This is a **working, manually invoked, bounded read/export**, not a scheduled
connector or certified ServiceNow integration. It builds on the offline
selected-incident Table API validation in
[ServiceNow staging](SERVICENOW_INCIDENT_STAGING.md) and produces the four
columns accepted by OWG's existing **Event logs → Import CSV** and
user-triggered append/deduplication workflow. No source-side writes, automatic
event-log append, credential storage, automatic checkpoint, historical replay
API or background polling are enabled.

## Explicit opt-in and credentials

Create an **owner-only** local JSON grant file (on POSIX: chmod 600). It is
operator-managed and must match the exact host, tenant namespace and 1–8
selected lowercase 32-hex incident IDs. Example, with synthetic IDs only:

```json
{
  "version": 1,
  "enabled": true,
  "consented": true,
  "host": "example.service-now.com",
  "tenant_scope": "local-support-sandbox",
  "sys_ids": ["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"]
}
```

This file is a **local opt-in marker**, not an authenticated ServiceNow
permission grant. The account token must separately have the minimum Table API
read ACL for the exact selected incidents. No password or token belongs in
the grant file, CLI arguments, output CSV or report. Set
`OWG_SERVICENOW_TOKEN` through your OS secret manager/environment only. Do
not run against a real tenant without the tenant owner's explicit permission.

Invoke from the repository root:

```sh
python -m server.servicenow_incident_sync \
  --instance example.service-now.com \
  --tenant local-support-sandbox \
  --incident-id aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
  --grant-file /private/selected-servicenow-grant.json \
  --consent-selected-read \
  --timezone UTC \
  --output /private/selected-incident-events.csv
```

The source instance's **actual** IANA timezone must be supplied. For example,
use `Europe/Stockholm` only if the ServiceNow tenant administrator confirms
that is the timezone used for `sys_updated_on` raw values. A declared timezone
is **not** independently verified by OWG. Ambiguous fall-back timestamps and
nonexistent spring-forward timestamps are refused rather than silently
assigned a UTC instant. For an incremental *current-snapshot* read, add
`--since "2026-10-30 03:10:00"`, in that same source-local clock. This is
a caller-supplied inclusive lower bound, **not** a persisted cursor.

The program performs **at most one** exact, read-only GET to
`https://<instance>.service-now.com/api/now/table/incident`, with selected
`sys_idIN` IDs, optional `sys_updated_on>=` lower bound, deterministic
ordering, and only `sys_id,sys_updated_on,sys_mod_count,state` requested.
The fixed response limit of 16 exceeds the maximum of eight selected IDs.
Unexpected/unselected rows, duplicate rows, a full page, invalid status,
redirects, foreign final URLs, malformed/oversized JSON or non-UTF-8 JSON
media types fail closed. Direct TLS uses system trust and hostname checking;
ambient HTTP proxies and redirects are disabled. There is a 15-second request
timeout and 256 KiB response cap. No arbitrary URL, table, query or
provider-supplied next-page link is followed.

The local grant file is re-read before the GET, after the response and
immediately before publishing the CSV. Removing it or setting
`enabled: false` revokes future runs and is checked at those boundaries.
This **cannot atomically fence an in-flight request or a concurrent revocation
between the final check and filesystem publication**, and is not a substitute
for centralized authorization or provider token revocation. For immediate
provider-side denial, revoke the ServiceNow token/ACL as well. Do not
interpret the local marker as proof of tenant ownership.

## Import, incremental retries and provenance

The new CSV contains only:

| Column | Meaning |
|---|---|
| `case_id` | Stable tenant/instance-namespaced hash of selected incident ID |
| `activity` | `Incident state: <raw state code>` (no description, caller or text) |
| `timestamp` | UTC instant computed from the **declared** instance timezone |
| `source_event_id` | Stable tenant/instance-namespaced hash of `sys_id:sys_mod_count` |

The output file must **not exist**. A fully written 0600 temporary file is
published under a new name using an exclusive same-filesystem hard link;
existing output is never overwritten. The JSON stdout report contains
counts and fixed provenance/unknown flags, not incident IDs, URLs, state
values, source private text or tokens. Treat even hashed keys and status
codes as potentially sensitive business information.

In **Event logs → Import CSV**, map the four named columns and choose the
same explicit source namespace on subsequent user-triggered appends. Stable
`source_event_id` values allow the existing append/dedupe path to skip
exact retries. A different `sys_mod_count` produces a different snapshot
event; this is **not** a historical status-change event. A gap in modification
counters means intervening source revisions may have been missed. Missing
selected incidents can mean ACL filtering, an unchanged record under
`--since`, or other source behavior; they do **not** mean deletion.
The importer and user, not this script, decide whether to retain or append.

Do **not** advance a checkpoint from the maximum returned timestamp: the
Table API returns current incident snapshots, not a complete history; a
same-second update, ACL omission or intervening revision can be lost.
For now, operators choose overlapping manual `--since` windows and
review omissions/revisions. Historical completeness, timezone authority,
source-provider account identity, customer business outcomes and source
chronology remain unverified. Consent to read does not authorize observation,
cloud upload, employee profiling, execution, or source-system writes.

## Acceptance and remaining gaps

Synthetic fake-transport tests cover exact URL/GET/token header, status and
redirect refusal, source envelope/size bounds, tenant/grant isolation,
revocation between read and publish, DST ambiguity, replay-stable keys,
no-overwrite and privacy-safe reports. They **never** call a real provider.
The original staging test suite's large JSON parameter now has a bounded
pytest display ID so Windows does not fail setting `PYTEST_CURRENT_TEST`.

Before enterprise rollout: independently verify provider ACL/tenant ownership
and timestamp semantics in an explicitly authorized disposable ServiceNow
sandbox; add centrally enforced, atomic grant/revocation and retention
lifecycle, persisted cursor with safe late-event/replay semantics, durable
event-log append integration, backoff/rate limits, observability and
provider-side change-history extraction. A passing synthetic test is not
real provider/customer acceptance.
