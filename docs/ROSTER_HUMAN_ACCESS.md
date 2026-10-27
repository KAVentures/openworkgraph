# Roster offboarding and human sign-in

An administrator's employee offboarding action now rejects subsequent human
requests for that exactly matched roster identity, including delegated OIDC
bearers, remote personal MCP authentication/tool rechecks and the separately
scoped process/analysis browser sessions. It also retains the existing device
credential and open-invitation revocation behavior. Evidence is not deleted.

The roster is optional. An unregistered person can still use explicitly
configured group permissions. A roster entry does not grant scopes or process
membership. Lookup is tenant-local and uses only the verified configured actor
identifier, exact linked SSO subject, or that identifier as a normalized roster
email. An arbitrary `email` claim is not used for association. Conflicting roster
matches are refused instead of selecting the first row. A known inactive
identity cannot fall back to unregistered group access.

Both browser portals now use the same exact roster actor mapping as the delegated
API, so a linked opaque SSO subject signs in as its roster actor. Existing
process ownership and analysis ACLs are not rewritten or merged. Relinking an
identity may require explicit administrator review of existing actor grants.
The current root/view ACL and configured group mapping remain separate checks.

## Sessions and rehire

A roster-linked browser session binds the exact opaque employee ID and current
revocation generation. Every offboarding increments that generation in the
same transaction that disables the employee, devices and invitations. The
counter does not depend on wall-clock ordering. An explicit reactivation does
not reset it, so even repeated offboard/rehire cycles or a backward/fixed clock
cannot revive old browser credentials. Fresh verified sign-in is required.

On each browser request, OWG checks active status, unique identity mapping,
the exact employee binding and current generation. Unbound legacy sessions
must sign in again once their identity matches a roster entry. A previously
bound identity whose roster mapping disappears or changes cannot silently
become an unregistered session. Ordinary display-name updates retain a valid
session when identity and revocation generation are unchanged.

Browser storage remains memory-only. Server storage adds only the optional
opaque employee ID and numeric generation to the existing minimized identity;
it retains no provider token or arbitrary email claim. The actor identifier can
itself be the roster work email, consistently with the existing roster model.
Old server session rows expire through the existing bounded cleanup; they are
unusable immediately after offboarding, not only when deleted.

## Upgrade and deployment

Startup adds the idempotent `gateway_employee_access_epochs` table with an opaque
employee primary key and bigint generation. Existing employee rows and events
are not rewritten. Absence of an epoch row means generation zero. There is no
new environment variable, external service, default permission or credential.
SQLite and PostgreSQL use the same transactional upsert. If its write fails,
employee/device/invitation disablement rolls back together.

Back up the database and stop older application workers before upgrading; every
worker must enforce current roster status and revocation generations. A rolling
mix with older workers cannot enforce this boundary consistently. Rolling back
to a version without these checks loses this protection even though the added
table can remain. No customer database was upgraded during development.

## Scope and limits

This is current OWG roster enforcement, not SCIM provisioning or provider
push-revocation. Disable the account and grants at the company identity provider
as part of offboarding. OWG cannot identify an unrelated/unlinked provider
identity as the same employee, revoke the provider's token, or remove data
already received by a client. Group changes at the provider retain their existing
sign-in/expiry behavior. A reactivated employee's still-valid delegated bearer
may authenticate again according to current configured group scopes; old OWG
browser sessions and device credentials remain revoked.

Authentication is checked on each subsequent request/tool call. It cannot recall
an already authorized response, cancel a completed write, or atomically span an
external business operation. Employee offboarding does not disable a separately
managed administrator account or unrelated service credential. Those retain
their own explicit revocation controls. Offboarding checks establish access
status, not process correctness, business outcomes or full enterprise parity.

Tests reproduce the previous live-token/session fallback, cover both databases,
tenant and optional-roster isolation, ambiguous identities, fixed-clock repeated
rehire, legacy sessions, epoch-write rollback, real delegated HTTP and remote MCP
authentication. Chromium also exercises actual owned RSA OIDC/PKCE sign-in to
both portals, live offboarding/page clearing, rejected new sign-in, an unaffected
unregistered peer and fresh sign-in after rehire. Real provider/IdP acceptance
and SCIM remain under #370/#264.
