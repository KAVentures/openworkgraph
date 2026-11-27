# Native Dynamics 365 / Dataverse audit feeds

Enterprise delivery #1572, R3/R20. Extends the common retained enterprise
connector runtime, Gateway jobs, exact snapshot publication and private
source-host crosswalks. It is not a Dynamics ERP replication service.

## Coverage

Ten standard logical table names are supported: account, contact, lead,
opportunity, incident, quote, salesorder, invoice, task and appointment.
Each selected table may have up to 100 exact record GUIDs. Empty record
filters require separate approval to read all visible matching audit rows.
The scope is one to ten selected tables and explicit audit operation codes:

| Operation | Code |
|---|---:|
| Create | 1 |
| Update | 2 |
| Delete | 3 |
| Upsert | 5 |
| Archive | 115 |
| Retain | 116 |
| RollbackRetain | 117 |
| Restore | 118 |

The default is create/update/delete. These are native audit metadata codes,
not independently verified business transitions. Audit access operations
and custom operations are intentionally excluded. The native action number
is preserved in the activity label, not translated into an inferred
approval or payment outcome.

## Deployment

Configure an Entra application and a Dataverse application user in the
customer's environment with the minimum audit-read and identity privileges.
OAuth scope alone does not establish read-only permissions. This adapter
only issues GET requests to Dataverse and the client-credentials POST to
Entra. Tenant-specific application authentication is separate from the
existing Microsoft 365 delegated connection; neither grant implies the other.

Provide an exact approved deployment catalog in OWG_DATAVERSE_CATALOG.
Example structure (replace all GUIDs and the environment origin):

    {
      "crm_production": {
        "origin": "https://customer.crm4.dynamics.com",
        "tenant_id": "<tenant GUID>",
        "client_id": "<application GUID>",
        "organization_id": "<Dataverse organization GUID>",
        "application_user_id": "<Dataverse application user GUID>",
        "credential_env": "OWG_DATAVERSE_SECRET_PRODUCTION"
      }
    }

Supply the actual client secret through the named environment variable.
Do not place it in the catalog or in URLs. This initial adapter supports
commercial Dynamics CRM environment origins of the form *.crm[N].dynamics.com
only, not sovereign clouds, custom origins, certificates or managed identities.
The catalog and secret are pinned to an immutable local profile. Changing
the catalog or the secret requires an explicitly reviewed replacement
profile. The access token itself is obtained on demand and is not retained
in the profile, Gateway, snapshot or crosswalk.

## Operator workflow

Open the enterprise connector operations dashboard and select **Create native
Dynamics 365 / Dataverse audit feed**. Review the exact environment, tenant,
application, organization and application-user IDs. Select audit tables and
exact record GUIDs, operations, initial time, advancing window, overlap and
reporting lag. Separately approve configuration, authentication and identity
checks, and audit metadata retention. If any table is unrestricted by
record GUIDs, separately approve that scope.

Saving a configuration makes no source request. Use a separately confirmed
manual read, recurring local schedule, or exact Gateway-managed job. The
initial timestamp must be within 365 days (OWG's bound, not a provider
retention promise). Advancing windows are 1–168 hours with a bounded overlap
and configurable reporting lag.

The adapter uses the tenant-specific Entra v2 token endpoint with the exact
environment /.default scope. Verify WhoAmI organization and application-user
IDs before and after the full multi-table read. The account must be
configured and authorized by the deployment owner; WhoAmI verifies the
returned organization and user, not every aspect of the permission model.

## Audit reads and exact retention

Fixed GET /api/data/v9.2/audits, selecting auditid, _objectid_value,
objecttypecode, createdon, operation, action and _userid_value.
No changedata, attributemask, old/new field values, record contents, names,
descriptions or audit detail actions are requested. Unknown extra fields
are not retained. If the returned record GUID, table, operation or
timestamp is outside the configured scope, reject the complete window.
An absent actor is retained without a resource identity.

Use a half-open createdon range, exact logical table and operation filters,
optional exact record GUID filters, and createdon/auditid ordering.
Audit IDs are deduplicated across selected tables. Equal-time GUID ordering
is provider-defined; OWG checks nondecreasing timestamps and distinct
identities without claiming a particular within-timestamp order.

The common budget is 20 requests (including token and WhoAmI), 10,000
projected rows, bounded response bytes and a nominal network deadline.
The requested page size is at most 500. Only an exact same-origin audits
continuation with a bounded skiptoken is accepted. Reject changed filter,
projection, order, repeated tokens or other continuation parameters.
Original query scope is reconstructed on each request. Actual service
paging compatibility requires sandbox testing; a different continuation
contract fails closed rather than broadening the source scope. HTTP 429
sets bounded cooldown. No redirect or environment proxy is used.

All selected tables must complete before retaining the window. The common
runtime commits events and checkpoint together, supports overlap replay
with exact source-key deduplication, and invalidates execution on changed
profile, credential or deleted retained dataset. This is not a source
transaction across tables and does not prove complete provider visibility.

## Cross-source and Gateway integration

Register the exact Dataverse profile in the Gateway connector jobs console.
The application secret and access token stay on the ingestion host.
Publication of an exact retained snapshot requires a separate grant.
For a source-host crosswalk, use the logical table as native_type and the
native record GUID as native_case_id. The private crosswalk resolver checks
the selected table and record scope and derives the same retained case key.
Crosswalk preparation also checks that the case belongs to the exact
approved snapshot. The Gateway receives pseudonymous match tags, not raw
Dataverse GUIDs or the shared crosswalk key.

## Acceptance and limitations

Auditing must be enabled at the relevant organization, table and column
levels. Retention, historical gaps, ACL visibility, late arrivals outside
overlap and audit-log deletion limit coverage. Delete operations are
returned audit metadata, not deletion reconciliation. No CRM record
replication, record relationships, historical field values, transaction
grouping, financial or ERP data, or automated secret rotation is claimed.
Some audit rows lack a record identity; this adapter fails closed rather
than manufacturing a case. No user-access auditing is included.

Regression fixtures exercise all ten tables, native request scope,
application token acquisition, pre/post WhoAmI, scoped pagination,
identity conflicts, atomic retained-log behavior, replay, crosswalk case
resolution, recurring registration, passive inventory and browser
configuration. Full CI and live Dataverse sandbox validation are required
before production use.

Microsoft references:
- https://learn.microsoft.com/en-us/power-apps/developer/data-platform/webapi/reference/audit
- https://learn.microsoft.com/en-us/power-apps/developer/data-platform/auditing/retrieve-audit-data
- https://learn.microsoft.com/en-us/power-apps/developer/data-platform/authenticate-oauth
- https://learn.microsoft.com/en-us/power-platform/admin/manage-dataverse-auditing
