# Native HubSpot selected-record history feeds

Enterprise delivery #1572, R3/R20. Builds on the common retained enterprise
connector runtime, local and Gateway scheduling, approved snapshot publication
and private cross-source case mapping. No HubSpot CRM write operations are
implemented.

## Supported sources

| Native CRM object | Evidence |
|---|---|
| contacts | Selected property-version timestamps and optional creation |
| companies | Selected property-version timestamps and optional creation |
| deals | Selected property-version timestamps and optional creation |
| tickets | Selected property-version timestamps and optional creation |

Choose one to four object types, up to 100 exact internal record IDs per
type, and one to twenty explicit internal property names per type. Object-wide
discovery and arbitrary queries are not supported. Each retained case is
the profile-local pseudonymous identity of the typed native CRM record.

The HubSpot batch read endpoint returns property values and associated
change-source metadata, including versions outside the requested event
window. Separate explicit consent is required for this transient receipt.
OWG retains only selected property-change labels, timestamps, pseudonymous
case and event IDs, and optional record-creation timestamps. It does not
retain or hash property values, source IDs, source labels, names, email
addresses, users or arbitrary response metadata. A property change is not
proof of a particular previous/new state or business outcome.

## Deployment source catalog

Create a dedicated private application in the customer HubSpot account.
Grant only required CRM object access. Configure a deployment-owned catalog
through OWG_HUBSPOT_CATALOG. Illustrative configuration:

    {
      "crm_production": {
        "hub_id": 123456,
        "app_id": 654321,
        "credential_env": "OWG_HUBSPOT_TOKEN_PRODUCTION",
        "scopes": [
          "oauth",
          "crm.objects.contacts.read",
          "crm.objects.companies.read",
          "crm.objects.deals.read"
        ]
      }
    }

Supply the actual private-app token through the named environment variable.
The token must begin with pat-; never store the token in the catalog or in
URLs. The catalog accepts only the listed supported read scopes, oauth, and
the legacy tickets permission. The latter may permit writes and requires
separate operator acknowledgement even though this adapter uses only fixed
read operations. A separate tickets read scope is accepted where supported.
Provider permissions must be checked in the tenant; the adapter cannot
guarantee that a scope name implies a read-only credential.

The immutable profile pins the exact catalog definition and token binding.
Changing either stops the profile and requires a newly reviewed configuration.
No automated token rotation or OAuth public-app flow is claimed.

## Operator workflow

In Enterprise connector operations choose **Create native HubSpot CRM history
feed**. Review the approved account, application and exact scope list, then
select typed record IDs, properties, optional creation events, initial time,
window, overlap, lag and batch size. Saving performs no provider request.

The first timestamp is within 365 days (OWG's initial bound, not a HubSpot
history-retention promise). Advancing windows cover 1–168 hours with bounded
overlap and lag. Requests are divided into exact batches of 1–100 records,
with the common 20-request budget including two account/scope checks.
Use a smaller selected scope if the byte or row bounds are exceeded.

A separate manual read confirmation, recurring local schedule or reviewed
Gateway job is needed for execution. Exact event publication requires its
own consent. Provider credentials remain on the ingestion host.

## Native read contract

All requests use HTTPS to api.hubapi.com, with redirects and environment
proxies disabled. Before and after every multi-object read, POST to the
private-app token-info endpoint, using tokenKey in the request body and
Bearer authorization. Require exact account (hubId), application (appId)
and scope-list equality. If token introspection is unavailable, the
connector fails closed. Test token-info availability with the actual
tenant and region before deploying.

Read only POST /crm/v3/objects/{approved object}/batch/read, with explicit
record IDs, properties=[hs_object_id] and selected propertiesWithHistory.
This is a read operation despite its HTTP method. No mutation endpoint is
available through this adapter. Require HTTP 200, COMPLETE batch status,
no reported errors, no unexpected pagination and exactly the requested
record-ID set. Archived, omitted or unexpected records fail the complete
window. Records and history versions outside the approved record/property
scope are refused. Requested properties omitted from a returned history
map are counted; this does not assert that they were never populated.

Property versions lack a native stable version ID in this projection.
OWG uses a deterministic metadata identity of typed record, property and
normalized timestamp. Two versions of the same property at the same
timestamp are ambiguous and fail closed, even if they fall outside the
retained window. This identity does not detect changes to a historical
value at an unchanged timestamp; values deliberately do not participate.
Optional record creation has its own identity and timestamp.

Every returned version, including ones outside the selected window,
counts toward the common bounded read budget. Only timestamps inside the
half-open window are retained. The receipt counts observed versions,
versions outside the window, missing requested properties and events by
object type. The entire window is atomic; a failure in one batch leaves
all prior batches uncommitted. Overlap replay uses exact source-event key
deduplication and the shared checkpoint transaction. HTTP 429 honors
bounded cooldown. No raw response bodies are retained.

## Cross-source and Gateway integration

Register the exact local profile with the existing Gateway connector job
flow. Publish only separately approved retained snapshots. For private
source-host crosswalks, use native_type=contacts, companies, deals or
tickets and native_case_id equal to the exact selected record ID. The
resolver verifies membership in the reviewed source scope and computes
the same retained case pseudonym. The existing snapshot membership check
still applies. No raw HubSpot record IDs or crosswalk shared key need
leave the source host.

## Acceptance and limitations

Property history reads are repeated snapshots of provider-visible versions,
not a complete append-only change feed. Provider history limits, visibility,
missing property history, merges/deletions and late versions outside overlap
can omit events. An accepted checkpoint denotes a completed bounded read,
not verified complete historical coverage. No record discovery, property
value transitions, deleted/merged record reconciliation, association joins,
archived record recovery, webhooks or continuous CDC is implemented. A
missing record blocks the selected profile until reviewed/reconfigured.

Backend fixtures exercise all four objects, native request scope, batch
membership, identity and scope drift, ambiguity, retained-log transactions,
replay and private crosswalks. A Chromium fixture configures the source
without contacting a provider. Full CI and live sandbox verification
remain required; this initial adapter uses the legacy CRM v3 batch API
and must be checked against customer API availability/version migration.

References:
- https://developers.hubspot.com/docs/api-reference/legacy/crm/objects/contracts/batch/get-contracts
- https://developers.hubspot.com/blog/a-developers-guide-to-hubspot-crm-objects-ticket-object
- https://developers.hubspot.com/changelog/crm-property-change-source-will-show-owner-id-instead-of-owner-email
- https://community.hubspot.com/t/requesting-private-access-token-information-returning-an-incorrect-response/85057
