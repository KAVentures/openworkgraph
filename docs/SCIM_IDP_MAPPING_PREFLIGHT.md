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

## Required acceptance after a clean static result

With a customer-controlled test tenant and explicit authorization, inspect actual generated SCIM requests against ServiceProviderConfig, Schemas and ResourceTypes; test matching filters, create/read/PUT/PATCH/deactivate/delete, Group identity if enabled, ETag/428/412 behavior, provider isolation, revoked-token refusal, employee offboarding/rehire, restore/downgrade and consent boundaries. Validate uniform worker versions before enabling optional mappings. Record failures and timestamps; synthetic CI or this local mapping file is **not** an Entra/Okta certificate, production pilot, customer outcome or business ROI evidence.

Relevant existing contracts: [Users](SCIM_USER_PROVISIONING.md), [names](SCIM_USER_NAMES.md), [search](SCIM_SEARCH.md), [Groups](SCIM_GROUPS.md), [client policy](SCIM_CLIENT_POLICY.md). Coordination: #458, #412, #326 Section B, #264 Stage 3.


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
