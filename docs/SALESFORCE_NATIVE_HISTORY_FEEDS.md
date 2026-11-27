# Native Salesforce CRM field-history feeds

Enterprise delivery #1572, R3/R20. This adapter extends the shared enterprise
feed runtime (#1573–#1578) and complements the existing selected CaseHistory
import and deletion-review tools. It is not a full CRM replication service.

## Coverage

| Native history object | Case identity |
|---|---|
| AccountHistory | AccountId |
| ContactHistory | ContactId |
| LeadHistory | LeadId |
| OpportunityFieldHistory | OpportunityId |
| CaseHistory | CaseId |

Each source uses a fixed SOQL projection: history ID, parent ID, CreatedById,
CreatedDate and Field. Select one to five objects, up to 30 explicit tracked
field API names per object, and optionally up to 100 exact parent record IDs
per object. If a selected object has no record-ID filter, separately approve
reads of all records visible to that integration user for the specified fields
and window. No arbitrary SOQL, names, descriptions, old/new field values,
business content or source writes are supported. Field names are retained as
namespaced activity labels; they are not proof that a particular business
transition occurred. History must be enabled and available for the selected
objects/fields. The adapter does not enable history tracking.

## Authorization and setup

1. Configure a Salesforce connected app/external client app and an integration
   user restricted to the required object and field reads. OAuth API scope
   itself is not a read-only grant; the Salesforce user and app permissions
   determine actual capabilities. OWG only issues GET requests.
2. In OWG Privacy → Business system sign-in, select Salesforce and explicitly
   check the additional identity-scope option. The requested scopes are api,
   refresh_token and id. Existing sign-ins are not silently expanded. A
   refreshable sign-in with API and identity access is required.
3. In Enterprise connector operations choose the native Salesforce feed.
   Review the exact API instance hostname, Salesforce organization ID,
   integration user ID, tracked fields and selected record IDs. The local
   profile is pinned to the stored connection ID and API host. The identity
   endpoint may return additional personal metadata transiently; only the
   organization and user IDs are checked, and raw responses are not retained.
4. Save the profile, then separately approve a bounded read and local
   retention, or configure recurring reads for that exact revision. Initial
   history is within 365 days (OWG's bound, not a provider retention promise),
   UTC whole-second timestamps, 1–168 hour advancing windows, configurable
   overlap and reporting lag. Request size 200–2000; actual provider page
   sizes may differ within the resource budget.
5. The same source profile can be registered as a Gateway-managed job and
   separately approved for the exact snapshot-publication flow introduced
   in #1578. Raw event sharing is not implicit in recurring execution.

## Native read and retention contract

The reviewed instance is a fixed Salesforce HTTPS hostname and must match
the stored OAuth API host. Before and after a complete multi-object read,
verify the reviewed organization/user with the Salesforce identity endpoint.
Credential refresh uses the existing OAuth store; recheck the profile and
instance after refresh before sending the bearer. No redirects, user-supplied
URLs, environment proxies or cross-host query locator forwarding.

Query only fixed history objects and approved identifier filters. Verify
returned record type, exact parent scope, field filter, actor ID and the
half-open CreatedDate window. Normalize case-sensitive 15-character IDs
and validate canonical 18-character checksum representations. A duplicate
record identity inside a single provider traversal is rejected rather than
silently treating a malformed traversal as complete. Continuation uses only
same-version relative query locators; reject missing, repeated, malformed
or cross-origin locators, inconsistent totalSize and incomplete results.

The common runtime bounds all five objects together to 20 pages, 10,000
records, 2 MiB per response, 16 MiB per window and a nominal 90-second
network budget. If one selected object fails, no partial window is committed.
Salesforce REQUEST_LIMIT_EXCEEDED responses trigger a one-hour cooldown;
HTTP 429 honors the shared bounded Retry-After seconds behavior.
Profile-local HMAC event, typed parent case and actor identifiers are
retained. A matching source event in an overlap is deduplicated by the
existing source-key index. A conflicting payload fails retention. The
checkpoint advances atomically with the retained events or a complete empty
window; interrupted runs retain the last committed checkpoint.

## Boundaries and acceptance

A successful query establishes only the returned records visible to the
integration user. History retention, tracking gaps, deleted records,
untracked changes, provider visibility and late arrivals outside overlap
limit completeness. No relationship joins between different CRM parent
types, opportunity stage values, historical field values, CDC, history
archive or deletion reconciliation are claimed. Existing separate
CaseHistory deletion-review tooling remains available.

The regression suite exercises multi-object retention, pagination, exact
record IDs, field filters, revoked grants and instance/identity drift,
rate limits, recurring scheduling and Gateway registration. Chromium
acceptance configures the source without contacting a real tenant. CI and
actual Salesforce sandbox validation are still required, including
the identity endpoint behavior, available field names, permission model
and provider-specific totalSize/page behavior.

Official Salesforce documentation:
- https://developer.salesforce.com/docs/platform/object-reference/references/associated-objects/sforce-api-associated-objects-history.html
- https://developer.salesforce.com/docs/platform/object-reference/references/standard-objects/object-opportunityfieldhistory.html
- https://developer.salesforce.com/docs/platform/api-rest/guide/resources-query
- https://developer.salesforce.com/docs/platform/mobile-sdk/guide/oauth-using-identity-urls.html
