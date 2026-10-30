# Bounded core SCIM names

User create, GET/list/search, PUT, PATCH and version checks support the optional
core `name` object. It is provider-owned descriptive metadata attached to the
existing employee linkage. It never derives or changes the roster's email,
stable actor, displayName, verified SSO identity, invitations, permissions or
consent. There is no automatic name-to-displayName mapping. Configure that
separate existing attribute deliberately if your organization needs it.

Supported strings are `formatted` (512 Unicode code points), and `familyName`,
`givenName`, `middleName`, `honorificPrefix`, `honorificSuffix` (256 each).
Structural keys are case insensitive and responses use the spellings above.
Strings retain their supplied case, Unicode and spacing. Controls, unpaired
surrogates, non-strings, case-colliding keys, unsupported children and bounds
violations refuse the request. Null subattributes are unassigned. An entirely
unassigned name is omitted; no name is invented from other identity attributes.

```json
{"name": {"formatted": "Dr. Jane Doe", "givenName": "Jane", "familyName": "Doe"}}
```

PUT replaces the complete name; omission or null unassigns it. Existing PUT
rules still require the immutable userName and never implicitly rehire an
inactive employee. PATCH add/replace of `name` or a pathless name object merges
specified children into the current name, preserving unspecified children.
`name.givenName` and the other supported child paths update one child. A null
whole-name value or `remove name` unassigns all children; a null child or
`remove name.givenName` unassigns that child. Removing an already absent name
or child is a successful no-op. Unknown/nested child paths and schema-prefixed
mutation paths remain explicitly unsupported. These bounded rules follow the
complex replacement semantics in [RFC7644 §3.5.2.3](https://www.rfc-editor.org/rfc/rfc7644.html#section-3.5.2.3).

```json
{"schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
 "Operations": [{"op": "replace", "path": "name", "value": {"givenName": "Janet"}}]}
```

The normal full-resource ETag covers the name. Real changes advance the existing
SCIM linkage revision and lastModified; identical updates preserve both. A
name-only change leaves the roster row and access credentials unchanged. A
concurrent roster edit still invalidates conditional writes, and a fresh name
delta preserves current human-edited displayName and status. All PATCH operations
stage before mutation. Name storage, lifecycle changes, revocation, revision and
minimized audits commit or roll back together. Audits contain no name values.
Strict versions remain the default. The separately approved standalone User
PATCH policy applies as documented; supplied stale versions and unversioned
Bulk/PUT/DELETE remain refused.

Schema discovery advertises these six children. Existing `attributes` and
`excludedAttributes` selection accepts `name` and its supported children for
GET and POST search; selected reads retain the full-resource ETag. Optional
[contact emails](SCIM_USER_EMAILS.md) have a separate bounded array contract. Name
filters, login aliases, password, unsupported complex attributes and extension
schemas are still refused. This is not full SCIM or real IdP certification.

The additive provider/employee-keyed table has no rows for old unassigned
resources, preserving their previous projections and versions. Current SQLite
backups retain name metadata and its version; normal schema initialization is
required after restore. Names follow the existing provisioning tombstone's
identity-retention lifecycle and are not evidence records. Deleted resources
remain inaccessible and cannot be silently adopted by a new provider.

**Upgrade all serving workers before mapping names.** Old workers cannot return
or conditionally version this new attribute and may apply different PUT/PATCH
semantics. The new worker refuses legacy name-unaware versions, but cannot make
an old worker safe or certify load-balancer routing. Mixed-version or downgrade
operation with named resources is unsupported; preserve a current backup and
validate the uniform deployment before enabling the mapping. This upgrade does
not itself enable provisioning, capture, sharing or source writes.

Owned SQLite/PostgreSQL, HTTP, concurrency, rollback, backup and browser fixtures
verify the bounded implementation. Customer-controlled name mappings, broader
identity attributes and company/pilot acceptance remain tracked in #458/#412/#264.
