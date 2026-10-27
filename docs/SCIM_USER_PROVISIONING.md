# Scoped Users provisioning

OWG offers a **bounded SCIM 2.0 Users subset** over the existing employee roster. This is not full RFC 7643/7644 conformance or certified Entra/Okta/other-provider compatibility. Discovery advertises the implemented User projection and operations; deployers must test their own provider's mappings and conditional-update behavior before rollout. Groups, Bulk, complex attributes and user-name migration remain separate work.

In the existing admin console, open **Identity → Identity provisioning**, create a labeled provider, copy its one-view credential into the intended identity provider, then explicitly enable it. New credentials are disabled by default. The console clears a revealed credential on navigation, refresh, provider-state changes and sign-out; it does not save it in browser storage or URLs. List/reload responses never return it again. Revocation is permanent; disabling is reversible under the current configuration revision. Existing administrator write-role checks apply to configuration. Provisioning credentials have no administrator, portal, evidence, process, device-enrollment or business-system execution authority.

Each provider is bound to one organization and receives its own `/scim/v2/{provider_id}` URL. Tokens are random, stored as hashes, checked against current enabled/revoked state, and scoped to that provider. Changing or revoking provider authority is serialized with its mutations. At most twenty providers, including retained revoked history, are supported per organization in this increment. No provider exists or is enabled automatically.

## Supported protocol

Primary references: [RFC 7643](https://www.rfc-editor.org/rfc/rfc7643) resource schemas and [RFC 7644](https://www.rfc-editor.org/rfc/rfc7644) protocol. Credentials use the Authorization Bearer header; query/cookie credentials are not accepted. Responses use `application/scim+json`, standard Error/ListResponse envelopes and `Cache-Control: no-store`. User responses carry Location, meta.location, ETag and meta.version.

| Endpoint below the provider URL | Support |
| --- | --- |
| `GET /ServiceProviderConfig` | Current advertised capabilities and bearer scheme |
| `GET /Schemas`, `/Schemas/{core-User-URN}` | Bounded supported User schema |
| `GET /ResourceTypes`, `/ResourceTypes/User` | Users resource type only |
| `POST /Users` | Create a new roster identity; returns 201 |
| `GET /Users/{id}` | Current provider-linked employee projection |
| `GET /Users` | List/page; `userName eq "…"` or `externalId eq "…"` filters |
| `PUT /Users/{id}` | Supported replacement projection under exact current If-Match |
| `PATCH /Users/{id}` | Atomic one-to-twenty add/replace/remove operations under exact current If-Match |
| `DELETE /Users/{id}` | Offboard and tombstone provisioning resource; returns empty 204 |

Pagination uses one-based `startIndex` and `count` from 0 through 200 (default 100). Count zero returns the current total without resources. Ordering is stable by normalized work email then employee ID; pages do not claim a historical snapshot across requests. Unsupported operators, sorting, attribute selection, repeated query parameters, groups, Bulk, extensions and unsupported paths explicitly refuse. This increment does not implement `.search` or generic SCIM filtering.

User inputs use the core User schema. Supported attributes are:

- `userName`: normalized work email, required at create/replacement and immutable once assigned. It remains the existing stable evidence actor. A rename needs a separately reviewed identity/history migration and is refused here.
- `displayName`: the existing roster display name, at most 120 characters. Whitespace follows the roster's normalization. Null/empty clears it. Replacement omission clears it. Removal, including repeated removal of an unassigned value, succeeds without inventing a change.
- `active`: explicit JSON boolean. Create defaults to true. Replacement omission preserves status, so omission cannot implicitly rehire. Unassigning/removing active is unsupported; use an explicit boolean.
- `externalId`: optional caller-supplied, case-sensitive string of at most 200 characters, unique within the provisioning provider; empty/null/removal clears it. It is **not an authenticated OIDC subject**. Common read-only `id`/`meta` echoed by a client are ignored.

Attribute and operation names are case insensitive. Add/replace accept a supported scalar path or a pathless object of supported attributes. Required/immutable fields cannot be removed. A remove without a path returns noTarget. Unknown attributes, case-colliding names, invalid booleans, controls, bounds violations, duplicate JSON keys, non-finite JSON numbers and malformed/deep JSON refuse the request. Input bodies are bounded to 64 KiB and accept `application/scim+json` or `application/json`. Passwords, emails arrays/aliases, names components, groups and extensions are not silently accepted or retained.

The exact current ETag is required for PUT, PATCH and DELETE: absent If-Match returns 428; a stale/mismatched/wildcard version returns 412. This is an intentional stricter deployment contract, not a claim of compatibility with clients that omit conditional writes. Ordinary administrator changes invalidate the projection's ETag too. No-op User updates preserve version and lastModified. After a lost response, read/filter the resource before retrying a conditional write.

## Identity, access and transaction boundaries

A provisioned User ID is the stable existing employee ID. The new tables hold provider credentials/configuration and provider-to-employee external-ID/version/tombstone metadata. They do not store a second roster, consent state, evidence history or SSO identity. Different providers/organizations cannot probe or mutate another provider's Users. Existing manual roster identities are not automatically adopted; create conflicts return 409 and cannot silently reactivate an offboarded person. The same restriction applies after deletion: tombstones do not authorize reassignment.

Deactivation reuses roster offboarding: employee state, device-token revocation, open personal-invitation revocation, browser-access generation and team clearing commit together. Rehire requires explicit current active=true. It preserves the stable actor and never restores old device, invitation or browser credentials. A newly authorized company sign-in can obtain a fresh browser session. Provider externalId never populates sso_subject; existing verified company identity/group rules still apply. Provisioning never enables Observe/FullAI, enrolls a device, changes sharing/retention, grants admin/process/analysis/evidence scopes or authorizes source-system actions.

Provider current-state checks, version checks, roster changes, external-ID linkage, offboarding and minimized audit share one transaction. Provider mutations lock provider then employee/link; ordinary roster changes use the same employee lock. SQLite uses the existing immediate writer transaction pattern, PostgreSQL row locks. PostgreSQL creation also serializes each organization's bounded provider inventory. A failed later operation, uniqueness check, team change or audit rolls everything back. Invitation issuance, enrollment, SSO-proof verification and device linking lock the employee before the invitation/device rows too. If issuance wins the lock, deactivation sees and revokes the resulting credentials; if deactivation wins, issuance observes inactive status and refuses. This prevents a late credential or invitation from escaping the sweep or becoming usable after rehire. Audits contain stable provider/employee IDs and action/state, not tokens, request bodies, passwords or supplied external IDs.

DELETE tombstones the provider resource and offboards the employee. It does **not** erase employee/evidence/ownership history or other personal data. Deleted resources return 404 and disappear from provider lists; external-ID lineage remains reserved. Credential revocation refuses future provisioning requests but does not undo previously completed roster changes or offboard an entire organization.

## Upgrade, backup and recovery

Startup adds two tables/indexes and is idempotent. Existing manual employees, teams, SSO links, capture/consent and workflow sources are not adopted or rewritten. Older application code can ignore these additive tables, but it cannot serve provisioning requests. Keep the existing roster-offboarding protections from #411 when rolling back; an older build is not a supported way to restore revoked access. Avoid mixed-version provisioning workers until the upgrade is complete.

[Legacy inactive-roster repair](LEGACY_ROSTER_AUTHORITY.md) revokes missed device/invitation credentials during roster startup and before explicit manual or SCIM rehire. It preserves current identity/evidence/history and rechecks employee status under the lifecycle lock. This repairs currently inactive persisted states; it cannot reconstruct an already-erased inactive history or supply out-of-band backup rollback protection.

Owned SQLite backup/restore and PostgreSQL 17 custom pg_dump/pg_restore checks preserve provider/link/tombstone, employee, epoch, team, token, invitation and audit rows exactly, including current revocations. This is not production company recovery certification. **Restoring an older backup can restore older authority**, including credentials and generations revoked after that backup. Isolate a restored database from traffic until current identities, provider/device/browser credentials and revocations have been reconciled or rotated. This feature has no out-of-band anti-rollback authority or automatic backup scheduler.

## Verification and remaining acceptance

Tests cover advertised HTTP discovery/create/query/update/delete, scoped authentication, disabled/revoked providers, current versions, atomic PATCH/no-op/removal/null handling, uniqueness, malformed input, audit/team rollback, concurrent updates, manual-edit drift, tenant/provider isolation and no implicit identity adoption. Both SQLite and PostgreSQL are exercised. Owned RSA/OIDC flows for the process and analysis portals confirm deactivation blocks old browser sessions and rehire does not revive those or device/invitation credentials. The admin browser covers consented enablement, one-view credentials, escaped labels, roster effects, WCAG/mobile, permanent revocation and a held old-session 401 arriving after a fresh sign-in.

Actual customer-controlled IdP mapping/conditional-client compatibility, group provenance/conflict semantics, broader SCIM attributes/filtering/search, multiple provisioning domains per employee/adoption, safe user-name migration, durable organization jobs, backup operations and real company rollout remain open. Synthetic tests do not certify those or full competitor parity.
