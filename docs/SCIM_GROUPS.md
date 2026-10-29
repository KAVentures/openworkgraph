# Bounded SCIM Groups (first increment)

This subset extends existing provider-scoped SCIM Users provisioning. It is disabled until an administrator explicitly enables the provider credential. All resources are scoped to **that provider**, not merely its organization. Group names and identities grant no OWG privileges.

Supported:
- Schemas and ResourceTypes discovery now advertise the Group resource alongside Users.
- Group create, read, list with bounded displayName/externalId equality string filters and pagination, exact If-Match PUT/PATCH/DELETE. PUT ignores echoed read-only id/meta fields; endpoint identity and the If-Match header remain authoritative. Explicit PATCH changes to id/meta are refused.
- displayName, optional provider-scoped externalId, and up to 200 distinct direct User members supplied with a stable User ID and type User. Members must be current active Users of the same provider.
- Patch paths displayName, externalId and members support replacement; adding direct members appends without dropping existing ones. Removing members clears the list, and removing an exact members[value eq "id"] selector removes only that member. A PATCH is atomic.
- Resource versions include effective members. The read-only Users.groups projection lists current provider-owned direct groups, and the User ETag changes with that projection. No-op updates keep the version. Deleted groups are retained as tombstones.
- Each membership captures the employee offboarding generation. An offboarded, deleted, foreign-provider or later-rehired User is omitted and cannot silently regain an old link. Current provider approval is needed to re-add them.
- Mixed [Users/Groups Bulk](SCIM_GROUP_BULK.md) supports successful earlier User creates in direct member values and the supported removal selector; no arbitrary value or URL rewriting.
- Group operations use the existing provider credential, revocation, database transaction and minimized audit. No name automatically maps to a team, authorization scope, evidence permission or execution grant.

Not implemented (broader acceptance remains under #412):
- Nested groups and generic SCIM filtering/attribute selection.
- Admin mappings from provider groups into OWG teams or scopes.
- Verified real Entra/Okta/other IdP compatibility, mixed-worker migrations, company deployment and broader standards conformance.

Keep manually managed teams and all SSO, privacy, evidence, retention and consent boundaries independent of these groups. An IdP may update membership metadata, but it is not a business-identity claim, approval or consent for recording/sharing.
