# Enterprise connector operations and customer REST event feeds

Part of #1572 (enterprise delivery), #326 and #370 R3/R20. This is the first
local control-plane increment, not a Gateway-managed multi-tenant ingestion
service or a general-purpose ETL system.

## Operator console

Open the main dashboard, Privacy → Business system sign-in → **Open enterprise
connector operations and customer REST feeds**. The inventory never refreshes
OAuth or reads a provider. It shows stored sign-in counts, reconnect-required
counts, exact local retained profile and dataset IDs, scheduler state, last
retained success and checkpoint age, local failures, revision mismatches and
unmatched schedules. The freshness threshold is an operator-entered review
criterion, not a verified source SLA. Local sources are read separately,
not in one cross-store transaction. An unavailable inventory source is reported
rather than treated as an empty successful source.

Supported inventory scopes: OAuth sign-ins (Salesforce, ServiceNow, Microsoft
365, Atlassian and Zendesk), SAP order-to-cash, Stripe configuration, retained
Jira profiles, folder feeds, agent-run logs and customer REST profiles.
CLI-only scopes are not enumerated. The inventory deliberately reads Jira
profile metadata without calling the existing status function that may renew
an OAuth token. No endpoint URLs, hostnames, raw labels, records, credentials
or provider response bodies enter the unified JSON export. Exact local IDs and
operational timestamps are included. Live provider health is always unknown.

## Customer API protocol

A customer adapter exposes a fixed HTTPS GET endpoint. IT explicitly lists
allowed endpoints in the OWG process environment:

    OWG_REST_ALLOWED_ENDPOINTS=["https://api.example.com/events"]
    OWG_REST_TOKEN_EXAMPLE=<read-only bearer supplied through deployment secrets>

Never put the bearer into the URL, dashboard, source mapping, issue or log.
The endpoint must be exact, HTTPS, with no userinfo, query, fragment, custom
port or ambiguous path. The HTTP client disables redirects and environment
proxies. The endpoint receives the configured read-only credential; the
deployment allowlist is the trust boundary (including deliberately approved
internal hosts). OWG does not accept a cursor as a URL.

Query parameters:
- updated_since: inclusive UTC change-time boundary
- updated_before: exclusive UTC change-time boundary
- limit: configured maximum records per page, 1–500
- cursor: opaque continuation token, absent on page one

Response (exact outer keys):

    {
      "records": [
        {
          "event_id": "immutable-source-event-001",
          "case_id": "customer-case-001",
          "activity": "Approval received",
          "event_time": "2026-11-25T10:00:00Z",
          "updated_at": "2026-11-25T10:05:00Z",
          "resource_id": "operator-123"
        }
      ],
      "next_cursor": null
    }

Map flat source field names explicitly, including an optional resource ID.
Event time and change time may be the same field for append-only sources.
The API must return every event in the requested change window, with a stable
immutable event ID, and a complete cursor traversal. This is a declared
provider contract, not independently verified completeness. Corrections to
a retained event's payload are refused; deletions are not detected. Use a
provider-specific reviewed correction/deletion workflow rather than overwrite.

Source event, case and resource IDs are HMAC-pseudonymized per profile.
Activity labels and event timestamps are retained as explicitly approved;
unmapped fields, raw responses and cursors are not persisted by OWG.
No other attributes are currently ingested. Source pseudonyms are not
cross-provider verified business record joins. Credentials remain in the
process environment; a keyed binding detects any changed credential.
Credential renewal currently requires a new profile (same-account
rebind and continuous credential rotation remain follow-up work).

## Lifecycle and failure recovery

Create an immutable profile with an initial change-time start within 366 days,
a 1–168-hour advancing window, overlap shorter than the window (0–1440
minutes), and bounded page size. Configuration does not read the provider.
Run one window only after separate read and local-retention approval.
The first nonempty window creates a retained log. Subsequent windows append
via the existing supplied-source-ID index, rejecting conflicting event
payloads and deduplicating overlaps. Empty complete windows advance without
creating a fabricated dataset. Backfill continues window by window; newer
runs include the approved late-change overlap.

Profile lease, configuration revision, credential binding and dataset identity
are rechecked before each page and in the event import/append transaction.
The profile and checkpoint are written in that same DuckDB transaction via
the existing transaction hook. An interrupted read leaves no partial import;
an expired lease can be retried from the prior checkpoint. A post-commit
quality-report error can require inspection of the saved checkpoint; it cannot
roll the committed checkpoint back or overwrite its success receipt.
The existing event-log deletion transaction disables and invalidates the
associated profile and clears its checkpoint. Removing a profile keeps
its retained log; remove its old schedule separately. Disable/re-enable
changes the revision and invalidates earlier recurring authorization.
A request already dispatched cannot be recalled, but its results cannot be
committed against an invalidated profile.

Limits: 20 profiles, 20 pages per window, 10,000 received records, 2 MiB per
page, 16 MiB per window, 90-second nominal network budget (individual
requests bounded), 120-second lease. Repeated cursors, invalid JSON, page
overflow, out-of-window change times, missing fields, conflicting IDs, denied
credentials and incomplete pagination fail without checkpoint advancement.
HTTP 429 records a bounded retry cooldown, honored by manual and scheduled
runs. HTTP-date Retry-After falls back to 60 seconds. No automatic retry of
a partial response is performed inside a run. Re-run the same window.

## Recurring ingestion

Use the existing local scheduler with separate recurring read/retention
confirmation and the exact reviewed profile revision. Interval 15–10080
minutes. Recurring REST runs skip when local recording is paused, respect
existing scheduler backoff/quiet-hours settings and pause on invalidated
profile revision. Recreate an obsolete schedule only after reviewing the
new profile. This is local single-computer scheduling, not distributed
worker or multi-instance scheduling.

## Acceptance and remaining limits

- tests/test_enterprise_rest_feed.py: real local DuckDB transactions, mock
  HTTPS transport, pagination and duplicate replay, failed partial pages,
  conflict rollback, cooldown, profile revocation, expired lease, deleted
  dataset, schedule pinning, passive inventory and authenticated HTTP routes.
- tests/browser/enterprise_connectors.cjs: authenticated Chromium inventory,
  configuration, explicit recurring consent, stale revision, download,
  removal, accessibility and mobile layout; no external provider request.
- CI runs the existing full regression suite and added browser acceptance.

Live customer API compatibility, provider completeness, account-verified
credential rebind, managed secret rotation, tenant Gateway execution,
distributed leases, cross-provider joins, full provider-specific history and
real enterprise-scale throughput still require independent acceptance.
