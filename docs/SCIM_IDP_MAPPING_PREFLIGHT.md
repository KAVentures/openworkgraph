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
- User create must include immutable stable work-email **userName**; supported descriptive create fields are displayName, externalId, active, name, six documented name children and the shipped whole-array `emails` contact metadata. User PATCH cannot rename userName. It accepts the bounded contact paths `emails`, `emails.value/type/display/primary`, `emails[type eq "literal"]`, `emails[value eq "literal"]` and a supported child after a single selector. These are **PATCH write paths**, not search filters or assertions that an address is verified. Unknown nested selectors, unsupported operators, extension/roles/password fields and create-time `emails.value` are blocked. This static tool does not validate actual array values, PATCH operation ordering, null/removal semantics or live client requests.
- Normal writes require exact If-Match ETags. Only standalone User PATCH may omit it **after explicit administrator approval**; the tool flags the potential overwrite race even when approved. Unversioned PUT, DELETE, Bulk and Group writes are not accepted as compatible.
- Groups are independently enabled and have only displayName/externalId matching. **Entra Group matching by displayName is blocked by default.** Optional `provider_policy.unique_group_names: true` declares that the separate [Group naming policy](SCIM_GROUP_NAME_POLICY.md) has been enabled and verified in the actual provider. The CLI does not configure or inspect that policy; it warns that this is an unverified declaration and still requires live testing. Old mapping files without this flag retain their previous behavior. The server constraint requires uniform worker upgrades and does not change existing exact matching-filter semantics or grant OWG permissions.

The contact-email array **shipped in #480** (resolving #470) and is documented in [SCIM_USER_EMAILS.md](SCIM_USER_EMAILS.md). This preflight now recognizes its bounded write-path shapes but **continues blocking all email-based matching**, including `emails[type eq "work"].value` in `users.match_attributes`. Provider contacts are unverified descriptive metadata, never roster email, SSO, actor, invitation routing, automatic employee adoption or login authority. A value-based PATCH selector receives a caution because it depends on current stored contact data. Do not put real addresses or customer information in a mapping declaration; reports intentionally omit attribute values. **Upgrade all serving workers before enabling contact mappings**; mixed-version workers and downgrade with contacts are unsupported.

Selector declarations use the shipped unqualified PATCH grammar: literal ASCII
spaces around `eq`, at most 64 decoded characters for a type literal and 320 for
a value literal. Structural names are case-insensitive; quoted JSON strings are
parsed unchanged before comparison. For example, the invalid JSON escape
`\U0041` must not become the valid `\u0041` through case folding. A decoded NUL
is refused, while an escaped literal backslash followed by `u0000` remains
ordinary literal text. C1 controls and U+FFFE/U+FFFF are refused in raw paths
and decoded literals, matching the shipped identity validation; U+FFFD remains
ordinary literal text. Differential tests compare these cases, Unicode lengths,
and separators with the actual shipped PATCH parser. The matching-attribute
counts remain separate from selector match objects, including on CLI requests
with nonempty PATCH declarations.

## Required acceptance after a clean static result

With a customer-controlled test tenant and explicit authorization, inspect actual generated SCIM requests against ServiceProviderConfig, Schemas and ResourceTypes; test matching filters, create/read/PUT/PATCH/deactivate/delete, Group identity if enabled, ETag/428/412 behavior, provider isolation, revoked-token refusal, employee offboarding/rehire, restore/downgrade and consent boundaries. Validate uniform worker versions before enabling optional mappings. Record failures and timestamps; synthetic CI or this local mapping file is **not** an Entra/Okta certificate, production pilot, customer outcome or business ROI evidence.

Relevant existing contracts: [Users](SCIM_USER_PROVISIONING.md), [names](SCIM_USER_NAMES.md), [search](SCIM_SEARCH.md), [Groups](SCIM_GROUPS.md), [client policy](SCIM_CLIENT_POLICY.md). Coordination: #458, #412, #326 Section B, #264 Stage 3.
