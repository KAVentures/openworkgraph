# Native ServiceNow operations feeds

Part of enterprise delivery #1572 (R3/R20). Builds on the shared enterprise
connector runtime and the Gateway job/publication path in #1573–#1579.
Complements, rather than replaces, selected-incident audit and journal tools.

## Native sources and supported case semantics

| Table | Evidence |
|---|---|
| incident | Incident field changes and optional record creation |
| problem | Problem field changes and optional record creation |
| change_request | Change-request field changes and optional record creation |
| change_task | Change-task field changes and optional record creation |
| sc_request | Service-catalog request field changes and optional creation |
| sc_req_item | Requested-item field changes and optional creation |
| sc_task | Catalog-task field changes and optional creation |
| sysapproval_approver | Approval-record field changes and optional creation |

The exact selected table is the case namespace. Audit changes and creation
events for the same table and sys_id share a pseudonymous case. Approval cases
are approval-record cases, not inferred parent request identities. Task-table
inheritance and cross-table relationships are not inferred. Field-change
labels do not establish the old/new state, approval result or successful
business transition.

## Configuration

1. Provision an integration account and registered OAuth client in the
   customer's instance, with read access to the required audit fields and
   selected source tables. Set the integration account to UTC and verify
   query boundaries in the tenant. The OAuth useraccount scope is not by
   itself a read-only permission grant; the account ACLs determine actual
   access. OWG only sends GET requests.
2. Connect ServiceNow using the existing Business system sign-in workflow.
   The native operations profile requires a refreshable useraccount grant
   and exact matching instance hostname. No unrelated OAuth scopes accepted.
3. In Enterprise connector operations select **Create native ServiceNow
   operations feed**, the exact stored sign-in and instance, and up to eight
   tables. Specify up to 30 tracked field names and up to 100 exact sys_ids
   per table. Choose audit fields, record creation, or both for each table.
   Approve audit and creation retention separately. An empty record list
   requires separate unrestricted-visible-records approval.
4. Choose a first UTC whole-second history timestamp within the past 365
   days (OWG's initial backfill bound), a 1–168 hour advancing window, overlap,
   reporting lag and page size. Repeat read is enabled by default; each
   stream is read twice to compare the exact projected rows. The minimum
   number of selected stream traversals must fit the shared page budget.
5. Saving performs no provider read. Approve an individual read and
   retention, or schedule a recurring job for the exact revision. For
   centralized dispatch use the existing Gateway connector worker
   instructions. For publication use a separately approved exact snapshot
   and disclosure grant. Neither dispatch nor OAuth consent alone authorizes
   Gateway event-data disclosure.

## Fixed requests and failure behavior

Audit reads use GET /api/now/table/sys_audit with a fixed projection of
sys_id, documentkey, tablename, fieldname, sys_created_on. Creation reads use
GET /api/now/table/<selected table> with sys_id and sys_created_on only.
The encoded query is generated from approved table, field and record-ID
allowlists, not arbitrary supplied query text. It uses half-open UTC
sys_created_on boundaries and deterministic ordering by sys_created_on
then sys_id. Display values and reference links are disabled. The connector
never sets sysparm_query_no_domain and never requests oldvalue, newvalue,
names, usernames, comments, short_description, approval result or other
business content. Only a fixed activity label, pseudonymous event/case ID
and UTC timestamp are retained; no resource identities.

Every response must provide exactly one nonnegative X-Total-Count header.
The count must remain consistent throughout that traversal, and each
offset page must contain the expected number of rows. The Table API can
apply its limit before ACL evaluation. A short page may therefore indicate
ACL filtering rather than end-of-stream. This connector refuses inconsistent
or short pages, missing projected fields, duplicate or out-of-order records,
scope mismatches, malformed dates and incomplete traversal. No provider
pagination links are followed; offsets are generated locally against a
fixed allowlisted HTTPS instance. Environment proxies and redirects are
disabled. When repeat verification is enabled, same-count changes between
traversals are detected by comparing projected records, but this is not a
transactional provider snapshot and does not prove completeness.

All table/mode traversals share the existing enterprise resource bounds:
20 pages total including repeat reads, 10,000 retained candidates, bounded
per-page and aggregate response bytes, and the common nominal read deadline.
Eight audit-only streams require at least 16 pages in repeat mode. A larger
scope can require narrower windows, larger page size (up to 500), or separate
profiles. Separate profiles have distinct case namespaces; do not assume
their cases can be joined automatically. Failure in any selected stream
leaves the entire window uncommitted. HTTP 429 honors bounded retry cooldown.
Overlap replay uses exact source-event keys. Credential or profile revocation
invalidates a pending run. The existing dataset/checkpoint transaction
commits retained events and checkpoint together.

## Limits and validation

The instance/connection binding is checked before and after each OAuth
refresh, but this adapter does not independently verify the provider's
authenticated principal, tenant/domain membership beyond the instance host,
account timezone, or provider audit configuration. The operator must verify
them. Visibility and retention of sys_audit depend on the tenant's
configuration and ACLs; some tables or field histories may not be available.
Creation evidence is limited to currently visible rows and does not recover
deleted records. Audit timestamps are recorded field-change times, not
verified business execution times. No deletion reconciliation, old/new
field values, parent-record joins, journal contents or write operations.
Late-arriving data beyond overlap and changes to previously retained history
are not silently reconciled.

Tests exercise native request contracts, eight tables, creation grouping,
actual retained-log transaction behavior, replay, count/ACL gaps, repeated
read drift, grant changes, rate limits, scheduling, Gateway registration
and dashboard configuration. The tests are not a substitute for live
ServiceNow sandbox acceptance. Verify actual query semantics, UTC handling,
audit access, count headers, object names and pagination in a customer
sandbox before production use.

Reference: https://www.servicenow.com/docs/r/api-reference/rest-apis/c_TableAPI.html
