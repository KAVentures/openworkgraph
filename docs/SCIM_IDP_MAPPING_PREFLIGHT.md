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

Run **python scripts/scim_idp_mapping_preflight.py mapping.json** or pipe strict JSON on standard input with **-**. The tool accepts at most 32 KiB and refuses unknown keys, duplicate/case-colliding JSON keys, invalid attribute lists, nonfinite JSON and unsupported mapping types. Results contain only generic attribute *positions*, diagnostic codes and counts, never input attribute values. Exit code **0** means no *known static* blocker (result requires_live_validation), **2** means a known blocker (blocked), and **1** means invalid input (invalid_input). **Exit 0 does not mean the client works.**

## What is checked

- User matching is limited to **userName** and **externalId**; email value selectors, name, extension and other matching selectors are **not** supported by the current baseline. Up to eight conjunction clauses exist in OWG's read-only matching API, but the tool validates field choices rather than generated filter syntax.
- User create must include immutable stable work-email **userName**; supported descriptive create fields are displayName, externalId, active, name and the six documented name children. User PATCH cannot rename userName; all other unsupported/extension/roles/password mappings are blocked. This tool does not validate request-body values or all PATCH semantics.
- Normal writes require exact If-Match ETags. Only standalone User PATCH may omit it **after explicit administrator approval**; the tool flags the potential overwrite race even when approved. Unversioned PUT, DELETE, Bulk and Group writes are not accepted as compatible.
- Groups are independently enabled and have only displayName/externalId matching. **Entra Group matching by displayName is blocked** because OWG currently does not enforce the uniqueness Entra requires. Groups never grant OWG permissions. Any enabled Groups mapping still requires a live test.

The preflight deliberately does not mark contact-email matching as supported merely because a separate contact-email implementation is under review in #470. Update this bounded assessment only after the shipped, advertised server schema and matching semantics are verified; descriptive contact aliases must not silently become login/adoption authority.

## Required acceptance after a clean static result

With a customer-controlled test tenant and explicit authorization, inspect actual generated SCIM requests against ServiceProviderConfig, Schemas and ResourceTypes; test matching filters, create/read/PUT/PATCH/deactivate/delete, Group identity if enabled, ETag/428/412 behavior, provider isolation, revoked-token refusal, employee offboarding/rehire, restore/downgrade and consent boundaries. Validate uniform worker versions before enabling optional mappings. Record failures and timestamps; synthetic CI or this local mapping file is **not** an Entra/Okta certificate, production pilot, customer outcome or business ROI evidence.

Relevant existing contracts: [Users](SCIM_USER_PROVISIONING.md), [names](SCIM_USER_NAMES.md), [search](SCIM_SEARCH.md), [Groups](SCIM_GROUPS.md), [client policy](SCIM_CLIENT_POLICY.md). Coordination: #458, #412, #326 Section B, #264 Stage 3.
