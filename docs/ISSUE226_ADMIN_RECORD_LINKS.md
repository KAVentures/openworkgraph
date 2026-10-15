# Issue 226: explicit administrator-approved record links

Company managers may establish an **explicit relation** between two separate
opaque record references already observed in retained organization evidence.
This does not automatically infer equivalence from names, labels, timing or
two employees touching the same ticket.

Endpoints:
- POST /v1/admin/record-links (admin-token only, both opaque refs observed)
- DELETE /v1/admin/record-links/{organization_id}/{link_id}
- GET /v1/record-graph/links?resource_ref=owg:r:... (integration evidence:read)

Mappings are unique and organization-partitioned. Actor-scoped integration
tokens cannot see a relation unless both refs are present in their own
retained evidence. A full-org integration retains the existing broader
evidence:read authority. Retention is re-evaluated on every request; if a
related reference is no longer found, the mapping is purged only when a
complete bounded scan establishes absence. An incomplete scan returns an
explicit coverage warning, and never silently deletes mappings.

Mappings are stored as opaque refs without native provider identifiers or
credentials. They are never handed to source providers as authority. A link
means an admin approved a relationship, NOT that the business outcome,
handoff acceptance or system-of-record identity was source-verified.
No implicit historical uploads are triggered, and source adapters remain
OFF unless separately consented.

Remaining production work: organization-owned provider OAuth, corroborating
source identity receipts, real helpdesk/billing outcomes, source-log audit
provenance, a live held-out agent benchmark and historical sync UX.
