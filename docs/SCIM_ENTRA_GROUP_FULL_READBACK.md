# Complete bounded direct-user Group readback: Microsoft Entra ↔ OWG SCIM

This operator-triggered command advances [#458](https://github.com/KAVentures/openworkgraph/issues/458), [#412](https://github.com/KAVentures/openworkgraph/issues/412), and [#326 B](https://github.com/KAVentures/openworkgraph/issues/326). Unlike the [selected-user Group reconciliation](SCIM_ENTRA_GROUP_RECONCILE.md), this checks the **entire direct-user membership** of each selected, small Entra Group and the corresponding **entire OWG SCIM Group User membership**. It does not create or synchronize identities, grant permissions, execute business actions, or write to either system.

## What the operator can verify

For 1–4 explicitly mapped Entra Group GUIDs ↔ OWG SCIM Group IDs and 1–8 explicitly mapped Entra User GUIDs ↔ OWG SCIM User IDs, provide independent expected active states and complete expected **direct user** membership for each selected Group. The command performs:

1. An authenticated OWG SCIM ServiceProviderConfig preflight.
2. A bounded Microsoft Graph `GET /v1.0/groups/{id}/members/microsoft.graph.user?$select=id&$top=100&$count=true` for each selected Group, with `ConsistencyLevel: eventual`. It requires an integer `@odata.count` equal to the returned list size, no `@odata.nextLink`, and at most 8 unique direct User IDs. Unexpected, missing, unmapped, duplicate or paginated members refuse the comparison.
3. A live OWG SCIM `GET /Groups/{id}` for each selected Group, checking the **entire** direct User member set against the explicitly mapped users, without silently ignoring unselected OWG members.
4. A live Graph `GET /users/{id}?$select=id,accountEnabled` and OWG SCIM `GET /Users/{id}` for every mapped User, verifying expected active state and reciprocal selected Group projection.
5. A final reread of the first Graph Group, first OWG Group and OWG provider authorization to catch some mid-read drift and revoked authority.

Microsoft documents the Group-member OData cast, `ConsistencyLevel: eventual` and `$count=true`: [List Group members, Microsoft Graph v1.0](https://learn.microsoft.com/en-us/graph/api/group-list-members?view=graph-rest-1.0). This checks **direct user objects only**, not nested/transitive membership, devices, service principals or other directory-object types. Graph's advanced directory queries can be eventually consistent; even matching reads are not an atomic cross-provider snapshot.

## Authorization and privacy

Only run against tenant and provider resources the operator is authorized to inspect. Independently obtain a current **read-only** Microsoft Graph bearer with sufficient selected User and Group membership read permissions, and a current provider-scoped OWG SCIM bearer. Set `OWG_ENTRA_READ_TOKEN` and `OWG_SCIM_PROBE_TOKEN` as environment variables. Three separate flags explicitly consent to Entra Group reads, OWG Group reads and cross-system reconciliation. The Entra JWT `tid` hint must match the confirmed tenant, but is not token authentication; Microsoft Graph authenticates each request.

Production Graph origin is pinned to `https://graph.microsoft.com`; OWG HTTPS host/provider must be explicitly confirmed. The loopback HTTP override exists **only** for disposable local acceptance tests. Ambient proxies, redirects, retries, malformed/duplicate-key/nonfinite JSON, oversized pages, unexpected status/MIME, and stale/revoked authority fail closed. This tool never enumerates a tenant, contacts Graph before consent and local preflight, performs a write, refreshes a credential, or persists provider data. The private manifest contains identifiers; secure it under organizational retention rules. Provider access logs may independently retain request URLs. The CLI emits aggregate counts/limitations only, never IDs, emails, names, bearer tokens or raw Graph responses.

## Private input manifest (UTF-8 JSON, at most 16 KiB)

The values below are **illustrative placeholders**, not valid provisioned mappings. Confirm every GUID and OWG resource ID independently; do not infer identities from names, email addresses or `externalId`.

```json
{
  "format": "openworkgraph.scim-entra-group-full-readback.v1",
  "provider_id": "DISPOSABLE_PROVIDER_ID",
  "entra_tenant_id": "11111111-2222-3333-4444-555555555555",
  "users": [
    {"entra_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "owg_id": "DISPOSABLE_OWG_USER_1", "expected_active": true},
    {"entra_id": "bbbbbbbb-cccc-dddd-eeee-ffffffffffff", "owg_id": "DISPOSABLE_OWG_USER_2", "expected_active": true}
  ],
  "groups": [
    {
      "entra_id": "cccccccc-dddd-eeee-ffff-000000000001",
      "owg_id": "grp_DISPOSABLE_OWG_GROUP_ID",
      "member_entra_ids": ["aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"]
    }
  ]
}
```

Here the first User must be the Group's **only direct user member** in *both* systems. The second User is a confirmed nonmember. If the Group contains any other direct User, the command refuses rather than silently excluding it. An inactive User cannot be listed as a Group member under OWG's current direct-membership contract.

## Operator command

```sh
export OWG_ENTRA_READ_TOKEN='CURRENT_AUTHORIZED_MICROSOFT_GRAPH_READ_BEARER'
export OWG_SCIM_PROBE_TOKEN='CURRENT_OWG_PROVIDER_SCOPED_BEARER'

python -m scripts.scim_entra_group_full_readback \
  --owg-base-url https://owg.example.test/scim/v2/DISPOSABLE_PROVIDER_ID \
  --confirm-owg-host owg.example.test \
  --confirm-provider DISPOSABLE_PROVIDER_ID \
  --confirm-tenant 11111111-2222-3333-4444-555555555555 \
  --consent-entra-group-read \
  --consent-owg-group-read \
  --consent-cross-system-reconcile \
  < /private/complete_direct_user_group_manifest.json

unset OWG_ENTRA_READ_TOKEN OWG_SCIM_PROBE_TOKEN
```

For N selected Users and G selected Groups, **2G + 2N + 4 read-only GETs** (8–28) are made. Every request has a 10-second timeout and bounded response size. Each selected Group must fit a **complete one-page direct-user result of at most 8 users**; larger groups and pagination are deliberately unsupported. No partial-success receipt is emitted. Success reports `selected_groups_direct_user_membership_match` with counts, `direct_user_group_parity=selected_groups_only`, `non_user_and_nested_membership=not_verified`, `snapshot_consistency=not_atomic_graph_eventual`, and `customer_idp_provisioning_compatibility=unverified`.

## Acceptance and remaining limitations

Committed integration tests exercise a disposable Graph-shaped real TCP server and OWG's actual production FastAPI SCIM routes, including a second actual OWG loopback TCP transport; full membership success, extra OWG member, missing Graph member, Graph pagination/count/duplicates/oversize/rate limits/redirect refusal, mid-read drift, provider revocation, private-manifest scope/duplicate rejection and consent/aggregate-only CLI output. **Synthetic HTTP integration is not authorized customer Entra acceptance.** Real Graph permissions, consistency behavior, larger Groups, provider client provisioning request shapes, identity migration, complete device/SSO/invitation offboarding, mixed-worker rollout and retention enforcement remain separate acceptance gates.

Do not merge without exact-head required native Ubuntu/macOS/Windows pytest, SCIM contract, browser/process collaboration, accessibility, scale, starter, packaging/release, resolved reviews, and current-main conflict checks. Group metadata never grants OWG roles, company access or agent execution authority.
