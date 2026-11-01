# Offline SCIM IdP mapping preflight (bounded contract)

This local tool checks **declared** Microsoft Entra, Okta or other SCIM mapping settings against OWG's currently documented bounded Users/Groups subset. It makes **no network requests**, accepts **no tokens or secrets**, never provisions a person, and cannot establish real IdP compatibility, correct company consent or rollout readiness. It is an early blocker detector, not a certification or substitute for provider-owned sandbox acceptance.

From a source checkout, create a local JSON file containing only field names and behavior flags (no real identities, URLs, emails, bearer tokens or customer data):

    {
      "client": "entra",
      "users": {
        "match_attributes": ["userName", "externalId"],
        "create_attributes": ["userName", "externalId", "active", "name.givenName"],
        "patch_attributes": ["active", "name.givenName"],
        "write_mode": "etag"
      },
      "groups": {"enabled": false, "match_attributes": []},
      "provider_policy": {"allow_unversioned_user_patch": false}
    }

Run **python scripts/scim_idp_mapping_preflight.py mapping.json** or pipe strict JSON on standard input with **-**. The tool accepts at most 32 KiB and refuses unknown keys, duplicate/case-colliding JSON keys, invalid attribute lists, nonfinite JSON and unsupported mapping types. The JSON parser also refuses nonfinite exponent overflow (for example `1e309`) and integers exceeding 128 decimal digits **before conversion**, including values in otherwise unused nested fields. Ordinary finite numeric tokens can parse but do not authorize unsupported mapping fields. The strict decoder also rejects lone UTF-16 surrogate escapes in **any JSON string or object key**, even unused nested data, and normalizes raw unencodable surrogate input from direct Python callers to a generic invalid-mapping error. Properly paired surrogate escapes (valid supplementary Unicode characters) remain accepted by the JSON decoder; this does not change which declared SCIM mapping fields are supported. Results contain only generic attribute *positions*, diagnostic codes and counts, never input attribute values. Exit code **0** means no *known static* blocker (result requires_live_validation), **2** means a known blocker (blocked), and **1** means invalid input (invalid_input). **Exit 0 does not mean the client works.**

## What is checked

- User matching is limited to **userName** and **externalId**; email value selectors, name, extension and other matching selectors are **not** supported by the current baseline. Up to eight conjunction clauses exist in OWG's read-only matching API, but the tool validates field choices rather than generated filter syntax.
- User create must include immutable stable work-email **userName**; supported descriptive create fields are displayName, externalId, active, name, six documented name children and the shipped whole-array `emails` contact metadata. User PATCH cannot rename userName. It accepts the bounded contact paths `emails`, `emails.value/type/display/primary`, `emails[type eq "literal"]`, `emails[value eq "literal"]` and a supported child after a single selector. These are **PATCH write paths**, not search filters or assertions that an address is verified. The exact core User schema prefix `urn:ietf:params:scim:schemas:core:2.0:User:` is also accepted **only** on contact PATCH paths, matching the shipped contact parser; this does not authorize schema-qualified User matching/creation, generic attribute paths, or foreign Group/extension URNs. Unknown nested selectors, unsupported operators, extension/roles/password fields and create-time `emails.value` are blocked. This static tool does not validate actual array values, PATCH operation ordering, null/removal semantics or live client requests.
- Normal writes require exact If-Match ETags. Only standalone User PATCH may omit it **after explicit administrator approval**; the tool flags the potential overwrite race even when approved. Unversioned PUT, DELETE, Bulk and Group writes are not accepted as compatible.
- Groups are independently enabled and have only displayName/externalId matching. **Entra Group matching by displayName is blocked by default.** Optional `provider_policy.unique_group_names: true` declares that the separate [Group naming policy](SCIM_GROUP_NAME_POLICY.md) has been enabled and verified in the actual provider. The CLI does not configure or inspect that policy; it warns that this is an unverified declaration and still requires live testing. Old mapping files without this flag retain their previous behavior. The server constraint requires uniform worker upgrades and does not change existing exact matching-filter semantics or grant OWG permissions.

The contact-email array **shipped in #480** (resolving #470) and is documented in [SCIM_USER_EMAILS.md](SCIM_USER_EMAILS.md). This preflight now recognizes its bounded write-path shapes but **continues blocking all email-based matching**, including `emails[type eq "work"].value` in `users.match_attributes`. Provider contacts are unverified descriptive metadata, never roster email, SSO, actor, invitation routing, automatic employee adoption or login authority. A value-based PATCH selector receives a caution because it depends on current stored contact data. Do not put real addresses or customer information in a mapping declaration; reports intentionally omit attribute values. **Upgrade all serving workers before enabling contact mappings**; mixed-version workers and downgrade with contacts are unsupported.

Contact selector declarations use the shipped unqualified **or exact core User-qualified** PATCH grammar: literal ASCII
spaces around `eq`, at most 64 decoded characters for a type literal and 320 for
a value literal. The prefix is recognized case-insensitively without changing
quoted selector bytes. Other schema prefixes, extra colons, qualified matching
and create declarations remain unsupported. A clean static result for a
qualified contact path is not evidence that any real IdP sends that path. Structural names are case-insensitive; quoted JSON strings are
parsed unchanged before comparison. For example, the invalid JSON escape
`\U0041` must not become the valid `\u0041` through case folding. A decoded NUL
is refused, while an escaped literal backslash followed by `u0000` remains
ordinary literal text. C1 controls and U+FFFE/U+FFFF are refused in raw paths
and decoded literals, matching the shipped identity validation; U+FFFD remains
ordinary literal text. Differential tests compare these cases, Unicode lengths,
separators and qualified/foreign URNs with the actual shipped PATCH parser. The matching-attribute
counts remain separate from selector match objects, including on CLI requests
with nonempty PATCH declarations.

The declared contact PATCH paths must also be **unique after interpreting the
shipped path grammar**. An unqualified path and its exact core User-qualified
spelling refer to the same target; so do selector strings whose JSON escapes
decode to the same value, or whose decoded values compare equally after the
shipped casefolded selector matching. The offline preflight refuses these
ambiguous duplicate write declarations rather than allowing an IdP mapping to
silently target one contact field twice. Distinct child fields, selector kinds
and distinct selector values remain separate. This is only a mapping-level
consistency check: it cannot predict a live client's PATCH operation ordering,
current contact values, no-match behavior or whether a real provider sends
such requests. Invalid mappings receive generic errors without echoing contact
values or addresses.

The preflight also emits a **non-blocking** `overlapping_contact_patch_paths`
caution when supported declarations may write overlapping contact data: whole
`emails` plus any nested path, a field affecting all contacts plus a selector
for the same child (or an entire selected contact), or an entire selected
contact plus a child path for the same decoded/casefolded selector. These are
not necessarily duplicate targets: the shipped PATCH parser accepts each
operation, but the result may depend on actual client operation order and
current stored contact data. The warning does **not** infer that different
selector values or different selector kinds identify the same contact. It
does not block legitimate mappings, change provider permissions or grant
identity authority. Verify operation ordering and no-match behavior with a
customer-authorized IdP test before rollout; the static report never echoes
declared selector values or email addresses.

## Optional Group creation mapping and externalId lineage (offline only)

When Groups are enabled, the mapping may now include an **optional**
`groups.create_attributes` array, for example:

```json
{
  "groups": {
    "enabled": true,
    "match_attributes": ["externalId"],
    "create_attributes": ["displayName", "externalId", "members"]
  }
}
```

The array describes **declared IdP mapping outputs**, not actual emitted
requests, authenticated identity, SCIM permissions or an enabled OWG
connector. The shipped `gateway/scim_groups.py` Group create endpoint
**requires `displayName`** and optionally accepts `externalId` and
direct-User `members` metadata. Other Group creation fields, including
roles, scopes and nested membership paths, are unsupported. A declared
creation mapping missing `displayName` is a **blocker**
(`missing_group_create_displayname`). If matching declares `externalId`
but the declared creation mapping omits it, the tool emits a **nonblocking**
`group_externalid_match_not_created` caution: the later provider-scoped
correlation cannot be demonstrated by this declaration. The preflight does
not infer that matching by `displayName` is safe or unique.

If enabled Group creation declares `members`, the preflight adds the
**nonblocking** `group_member_id_resolution_unverified` caution. OWG's shipped
Group membership handler resolves each `members[].value` as the **current
provider's OWG SCIM User resource ID** (the `id` returned by User create/read),
not `userName`, `externalId`, an email address, a Group display name or an
OIDC subject. An IdP may correctly retain and send those IDs, but merely
declaring a `members` mapping does not demonstrate it. Before enabling
membership sync, in an explicitly authorized disposable tenant: create two
Users under provider A, read and record their returned opaque IDs, create a
Group with those IDs, read back direct membership, then verify that a
userName/externalId in place of an ID and a User ID belonging only to provider
B cannot silently attach. Test offboarding, rehire, source revocation and
stale ETags without granting OWG roles or access. Record actual client
requests and outcomes separately from this offline declaration. Omitting
`members` from a declared create mapping does **not** establish that later
Group PATCH membership updates are supported or absent.

For **older mapping files** with enabled Groups but no
`groups.create_attributes` field, the tool emits a **nonblocking**
`group_create_mapping_not_declared` caution rather than inventing a
creation contract. Adding an empty array is **not** equivalent to omitting
the declaration: it explicitly fails the required displayName check.
Any Group creation declaration while `groups.enabled=false` is blocked,
even an empty array, to avoid contradictory rollout settings. Existing
Group name-uniqueness, ETag, disabled-by-default, provider/tenant and
consent rules are unchanged; Group members never grant OWG access.

A clean declared mapping is still not proof that Entra/Okta sends
`externalId` consistently, uses the same provider-scoped value on later
filtering, sends `displayName` on creation, handles duplicate names, or
respects offboarding and ETags. With authorization and disposable IdP
identities, verify create → read → match → PATCH/PUT with current/stale
versions, cross-provider collisions, deleted/revoked providers, direct
member scope and backup/restore before company rollout. The preflight
performs **no** live provider calls, identity mutations or business writes.

## Required acceptance after a clean static result

With a customer-controlled test tenant and explicit authorization, inspect actual generated SCIM requests against ServiceProviderConfig, Schemas and ResourceTypes; test matching filters, create/read/PUT/PATCH/deactivate/delete, Group identity if enabled, ETag/428/412 behavior, provider isolation, revoked-token refusal, employee offboarding/rehire, restore/downgrade and consent boundaries. Validate uniform worker versions before enabling optional mappings. Record failures and timestamps; synthetic CI or this local mapping file is **not** an Entra/Okta certificate, production pilot, customer outcome or business ROI evidence.

Relevant existing contracts: [Users](SCIM_USER_PROVISIONING.md), [names](SCIM_USER_NAMES.md), [search](SCIM_SEARCH.md), [Groups](SCIM_GROUPS.md), [client policy](SCIM_CLIENT_POLICY.md). Coordination: #458, #412, #326 Section B, #264 Stage 3.


## Declared externalId matching lineage (offline only)

When `users.match_attributes` declares `externalId` but
`users.create_attributes` omits it, the preflight emits a **nonblocking**
`externalid_match_not_created` caution. SCIM `externalId` is a
**client-issued identifier scoped to its provisioning domain**, not a
verified email, SSO subject, global person ID, or invitation/adoption grant.
A declared create operation that omits it does not demonstrate how a later
`externalId`-based lookup will find that newly created resource.

This applies both to externalId-only matching and to mappings that also
declare `userName`: the latter may have a separate stable-userName fallback,
but the static declaration cannot establish that the IdP actually uses it.
Declaring `externalId` on creation removes this *particular* caution; it
does **not** prove that an IdP sends a stable value, that it is unique within
the current provider, that subsequent matching works, or that tenant and
provider boundaries are enforced. The server still refuses silent roster
adoption and userName renaming. Test create → read → match → update,
duplicate/foreign-provider IDs, offboarding, rehire, ETag conflicts and
revoked credentials using explicitly authorized disposable identities.
No client requests or provisioning are performed by this preflight.

## User creation activation mapping review (offline only)

A declared User **create** mapping without `active` now receives the
nonblocking `creation_active_not_declared` caution. In the shipped SCIM
User creation implementation, an otherwise valid new User is **active by
default**; only an actual create request carrying the literal JSON Boolean
`"active": false` immediately offboards that newly created employee. Thus
an IdP mapping that omits `active` cannot demonstrate that a disabled
upstream user will be created inactive in OWG. This is distinct from the
PATCH deactivation warning below: declaring `active` for updates does not
ensure it is sent on creation, or vice versa.

The preflight does not block otherwise valid create mappings, because
a separately validated client lifecycle may create then deactivate, or
may only provision active accounts. Declaring `active` removes this
particular caution but does **not** prove a real Entra/Okta request contains
the correct Boolean, that an inactive account cannot access OWG, or that
revocation works. Before rollout, explicitly test a disabled upstream
test employee's create request and OWG status, followed by deactivate,
rehire, ETag and credential-revocation checks with authorized fake/test
identities. No real IdP calls, identities or permissions are changed here.

## Deactivation lifecycle mapping review (offline only)

A declared User PATCH mapping without the `active` attribute now produces the
nonblocking `deactivation_patch_not_declared` caution. This includes mappings
that otherwise pass the supported attribute and ETag checks, and applies to
declared Entra, Okta or other clients. It identifies a **missing declaration**:
the preflight cannot demonstrate that a deactivation request will be sent via
the declared PATCH attributes. It does **not** prove that an IdP cannot
deactivate through a separately supported and tested PUT or other lifecycle
operation. Conversely, declaring `active` removes this particular caution
but does **not** prove the IdP sends the request, that the current service
credential has the correct scope, or that OWG offboarding/revocation succeeds.

Before any company rollout, use a disposable, customer-controlled IdP and
employee to exercise disabled-by-default authorization, create, deactivate,
rehire without old credentials, current/stale ETags, tenant/provider isolation,
revocation, audit and backup/restore. Inspect actual source-scoped results;
synthetic mapping validation is not live Entra/Okta conformance or permission
to provision identities. No provisioning, provider requests or identity
mutations are performed by this preflight.


## Structural Unicode alias rejection (offline parser parity)

The preflight now mirrors the shipped SCIM parser's Unicode `lower()`
normalization for plain attribute names and the optional core User URN
prefix. Python's broader `casefold()` can turn `uſerName` into
`username`, or `paramſ` into `params`, even though the actual OWG
User attribute parser and contact-prefix stripper do **not** accept those
spellings. Such declarations now remain unsupported blockers rather than
producing a misleading clean static result. Ordinary ASCII case variants
and Unicode **inside quoted contact selector values** retain their existing
behavior. Synthetic differential regressions compare these decisions with
the shipped User key parser and contact PATCH path parser.

This closes a static false-positive compatibility gap only. It does not
validate IdP-generated payloads, enable new mappings, alter actual SCIM
requests, or certify Entra/Okta compatibility. Strict server-side identity,
provider, tenant, ETag and revocation rules are unchanged.


## Group displayName collision review for Okta and other clients (offline only)

When Group provisioning is declared enabled and Group matching uses
`displayName` without a declared `provider_policy.unique_group_names: true`,
the preflight now emits `group_displayname_collision_risk` for **Okta and
other** clients. This is a **nonblocking caution**: Group names may collide
within a provider, making name-based matching ambiguous. Unlike Microsoft's
Entra requirement, uniqueness is **not a general SCIM requirement** and the
preflight does not presume every other IdP needs a uniqueness constraint.
Entra's existing `entra_group_name_uniqueness` blocker remains unchanged.

Matching only by `externalId` does not trigger the displayName collision
caution. Declaring the optional unique-name policy suppresses the caution but
still emits `declared_group_name_policy` and `groups_not_certified`: this
JSON flag neither enables nor verifies the actual default-off server policy.
Before rollout, an authorized administrator must check the provider's current
policy/revision, uniformly upgraded workers, existing active-name collisions,
real IdP filter behavior and tenant/provider isolation. No Group identity,
membership, login or permission is granted by a clean static report. These
synthetic mapping checks do not establish live Okta/Entra interoperability.


## ETag mapping with declared missing-If-Match allowance (offline only)

A declaration can say `users.write_mode: "etag"` while also saying
`provider_policy.allow_unversioned_user_patch: true`. These statements
are not mutually exclusive: the client may normally send `If-Match`,
while the **separately configured server policy** can still accept a
standalone User PATCH with the header **absent**. The shipped policy is
strict by default, is provider-scoped and administrator-controlled, and
does not relax supplied stale/empty/wildcard versions, PUT, DELETE,
Groups, Bulk or direct callers. See [SCIM_CLIENT_POLICY.md](SCIM_CLIENT_POLICY.md).

The offline preflight now emits the **nonblocking** caution
`etag_mode_with_unversioned_patch_policy` when these two declarations
coexist. It does not assert that the actual provider policy is enabled,
that the IdP ever omits `If-Match`, or that a particular request was
accepted. If the declaration intends strict ETag-only behavior, set the
declared allowance to false **and independently verify** the current
provider's stored policy and revision through the authorized admin
controls. A JSON declaration does not disable a previously enabled
server policy.

Before rollout, with explicit authorization and a disposable identity,
test a supplied correct ETag, a stale ETag, an absent header, and
disable/revoke races against the **current provider revision**. Check
that a missing header is rejected under strict policy and accepted only
where an administrator explicitly enabled the bounded standalone User
PATCH exception. Inspect tenant/provider isolation, offboarding
revocation, audit and worker-version consistency. These are required
live gates, not performed by this static preflight or its synthetic
regressions. No identity, credential, consent or permission changes are
made by the warning.


## Unicode contact PATCH structure versus shipped dispatch (offline only)

The shipped User PATCH handler first routes contact paths through
`scim_user_emails.owns()`, which recognizes the exact core User-qualified
prefix and lowercases the remaining path. The contact path regex itself uses
Unicode case-insensitive matching, which is broader: for example it can match
`emailſ[type eq "work"].value` as though the long-s were ASCII `s`, even
though the dispatcher does **not** own that path. It can also match
`emails[type eq "work"].prımary` or `diſplay` as a child, while downstream
canonical field validation does not recognize those spellings. The preflight
now refuses these structural aliases rather than incorrectly reporting a
compatible PATCH mapping. It retains ordinary ASCII case-insensitive
`EMAILS[TYPE EQ "work"].PRIMARY` and core User-qualified contact paths,
and never changes quoted selector literal bytes or their matching semantics.

Synthetic differential checks exercise the shipped ownership predicate,
contact path parser and canonical field set. These checks only reduce false
static compatibility claims; they do not exercise a live IdP request,
validate a complete PATCH transaction, grant contact identity authority,
change provisioning permissions or establish customer rollout readiness.


## Optional Group PATCH declaration (offline only)

An enabled Group mapping can optionally declare \`groups.patch_attributes\`, for example
\`["displayName", "externalId", "members", "members[value eq \"opaque-user-id\"]"]\`.
This declaration is **not** an executable PATCH request and does not specify
actual verbs, ETags, credentials or IdP behavior. Older mapping files without
the field remain valid and receive no implied Group PATCH certification.
An explicit Group PATCH declaration while \`groups.enabled=false\` is blocked,
even if its attribute list is empty.

The static preflight accepts only the shipped Group PATCH path subset:
\`displayName\`, \`externalId\`, \`members\` and the literal
\`members[value eq "opaque-user-id"]\` selector. Structural matching follows
the shipped ASCII syntax and exact spaces, allowing ASCII case variations but
not Unicode lookalikes, alternate selectors, nested child fields, schema URNs
or arbitrary extensions. Selector literals must be bounded, nonempty Unicode
scalar strings without controls. Rejected paths appear only as generic
indexed \`unsupported_attribute\` findings; raw IDs or selector values never
appear in reports. These conservative static constraints do not change or
certify the server's runtime parser.

**Crucially, the shipped Group selector path is supported only with the
\`remove\` verb.** A declared selector therefore produces the nonblocking
\`group_member_selector_remove_only\` caution: the preflight cannot establish
that the IdP emits removal rather than add/replace, or that a matching member
is still present. Any declared member PATCH target also produces
\`group_member_patch_ids_unverified\`: Group members use the current
provider's OWG SCIM User resource IDs, not emails, usernames, external IDs or
SSO subjects. A valid path is never proof of correct identity resolution,
tenant isolation, current provider authorization or Group membership success.
Group membership is metadata and **does not grant OWG permissions**.

For actual authorized acceptance, capture disposable IdP-generated Group
PATCH requests and check exact verbs/paths, current and stale If-Match
responses (428/412), direct User ID resolution, provider isolation, duplicate
and missing members, offboard/rehire, revocation, rollback/restore and mixed
worker versions. Never infer Group PATCH support from Group create attributes.
No live provider call, provisioning, identity mutation or business write is
performed by this offline preflight.
