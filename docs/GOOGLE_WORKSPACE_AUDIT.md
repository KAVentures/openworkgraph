# Google Workspace native audit ingestion

Extends enterprise program #1572 and the common enterprise feed runtime
from #1573. The Google Admin SDK Reports API is a native provider, not a
customer REST shim. It reads supported audit metadata for Drive, Calendar,
Admin console, Login, Gmail, Chat and Meet. Availability, reporting latency,
retention and access depend on Workspace edition and administrator privileges.
This is not a Gmail mailbox, Chat history, Drive document content or Calendar
event-content connector.

## Administrator setup

1. Enable the Admin SDK API in your Google Cloud project, configure the OAuth
   consent screen and register a desktop OAuth client. Restrict its use to the
   approved administrator account and comply with the customer's Google
   Workspace policy. The connector requests exactly
   `https://www.googleapis.com/auth/admin.reports.audit.readonly`.
2. In OWG Privacy → Business system sign-in, choose **Google Workspace audit**.
   Enter the registered client ID and client secret if required by the app.
   Complete the local loopback PKCE flow. Google authorization uses offline
   access and explicit consent; tokens use OWG's existing encrypted store.
3. In **Enterprise connector operations**, create a native audit profile.
   Choose the stored Google sign-in, exact Workspace customer ID (not the
   my_customer alias), application, and scope: exact numeric user profile ID
   or all. Domain-wide reads require a separate explicit confirmation.
4. Choose a case notion and initial audit date within 179 days. Choose an
   advancing window, overlap and reporting lag. Approve local audit retention.
   Drive document grouping additionally requires consent for the transient
   ordinary event parameters used to find the exact document ID.
5. Saving a configuration does not contact Google. Confirm one read and local
   retention to import the next window, then review the retained event log.
   Recurring reads use the existing explicitly approved, revision-pinned
   local scheduler. No provider writes are enabled.

## Native provider contract

The fixed endpoint is
`https://admin.googleapis.com/admin/reports/v1/activity/users/{userKey}/applications/{applicationName}`.
Requests include exact customerId, startTime, endTime, maxResults, a restricted
fields mask and opaque pageToken. Redirects and environment proxies are
disabled. Returned customer and application are checked against the profile.
Selected-user reads require exact actor profile ID readback; missing or
mismatched actors refuse the entire window. All-domain reads allow missing
actors and use an explicit fallback case. A successful empty response is
not independent evidence of customer activity or historical completeness.

All event envelopes use the provider's customer, application, timestamp and
uniqueQualifier for identity. Each reported subevent is projected to a
namespaced activity label, pseudonymous case and optional pseudonymous actor.
Sorted projected occurrences supply stable local subevent indices, including
duplicate subevents. Google does not issue those local indices. Reordering
does not create new events. Payload conflicts on retained keys refuse the
append; source additions, removals and corrections are not fully reconciled.

Case notions:
- **Actor**: exact actor profile ID, pseudonymized; missing actor falls back
  to the audit envelope.
- **Audit record**: one pseudonymous case per provider activity envelope.
- **Drive document**: exact doc_id parameter, pseudonymized; missing document
  falls back to the audit envelope; conflicting doc_id parameters refuse the
  window. This groups audit events about a file, not a verified business case.

Activity labels and timestamps are retained. Email, IP address, document
title, mail/chat text, other parameter values, tokens and raw responses are
not persisted. Drive document grouping requests ordinary string parameters
transiently because the API cannot select a parameter by its name in the
partial-response field mask. Sensitive audit-data options are never enabled.
Pseudonyms are profile-local and not verified cross-provider identities.

## Incremental behavior

The shared feed transaction commits events and checkpoint together, uses
exact source-event-key deduplication, and invalidates a run if its profile,
grant or retained log changes. The Google adapter requests a reporting-lag
cutoff and overlap to allow some delayed events. It refuses to jump over a
checkpoint outside the provider's 180-day historical horizon. The provider
may impose shorter service-specific availability and additional restrictions;
no API success establishes full historical completeness or a source SLA.
Google event time is used as the extraction boundary; events reported later
than the configured overlap may be missed. The scheduler pauses on revoked
or changed grant/profile. Renewal of the same OAuth connection preserves
the profile; replacing the connection requires a new profile. No distributed
Gateway ingestion is introduced here.

## Acceptance

`tests/test_google_workspace_audit.py` exercises all seven native application
routes against injected HTTP fixtures and real retained DuckDB operations:
pagination, overlap, duplicates, privacy projection, tenant and user mismatch,
revocation, expiration, conflict rollback, scheduler and OAuth PKCE/refresh.
Full CI, real Google administrator sandbox acceptance, actual edition support
and measured production throughput remain required. No live Google tenant
credentials were used in development.

Official API references:
- https://developers.google.com/workspace/admin/reports/reference/rest/v1/activities/list
- https://developers.google.com/workspace/admin/reports/v1/appendix/activity/drive
- https://developers.google.com/workspace/admin/reports/v1/guides/manage-audit-login
- https://developers.google.com/workspace/admin/reports/reference/rest/v1/activities
