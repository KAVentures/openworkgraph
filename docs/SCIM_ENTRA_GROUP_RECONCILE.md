# Selected Microsoft Entra direct Groups and OWG SCIM membership readback

This is a **real, operator-triggered, read-only comparison of two live systems**, not automatic provisioning or proof of complete IdP compatibility. It extends [selected lifecycle readback](SCIM_ENTRA_LIFECYCLE_RECONCILE.md) with direct Group membership for a deliberately small set of mapped users and groups. It advances [#458](https://github.com/KAVentures/openworkgraph/issues/458), [#412](https://github.com/KAVentures/openworkgraph/issues/412) and [#326 B](https://github.com/KAVentures/openworkgraph/issues/326). No SCIM core, contact-email, roster, permissions, consent or source-sync behavior is modified.

## What is verified

For **1–8 independently mapped Entra / OWG Users** and **1–4 independently mapped Entra / OWG Groups**, the operator supplies each selected user's expected active state and each selected Group's expected **direct membership among the selected users only**. The tool compares:

1. Live Microsoft Graph GET /v1.0/users/{id}?$select=id,accountEnabled with the corresponding live OWG SCIM GET /Users/{id} active state.
2. Live Graph GET /v1.0/users/{id}/memberOf/microsoft.graph.group?$select=id&$top=100&$count=true with the selected Group memberships in OWG's SCIM User groups projection. Microsoft documents **direct**, not transitive, membership: [List a user's direct memberships](https://learn.microsoft.com/en-us/graph/api/user-list-memberof?view=graph-rest-1.0). The read refuses a response with an @odata.nextLink rather than pretending a truncated page is complete.
3. Live OWG SCIM GET /Groups/{id} member lists against the expected **selected** OWG Users, including the reciprocal User view. Unselected users and groups are never declared absent or equivalent.

The command performs a bounded final reread of the first Graph user's active state and direct memberships, the first OWG Group, and OWG provider authorization. This reduces but does not eliminate read-time races. The systems have **no atomic cross-provider snapshot**. Graph OData-cast membership requests include ConsistencyLevel: eventual and $count=true as required by Microsoft Graph, and these indexed reads can be eventually consistent; a transient disagreement is a refusal, not permission to retry writes.

## Authorization and privacy

Obtain permission to inspect the specific Entra and OWG accounts and group memberships. Set **independent current credentials** in environment variables OWG_ENTRA_READ_TOKEN (Microsoft Graph bearer authorized for User and direct Group membership reads, with least necessary privileges) and OWG_SCIM_PROBE_TOKEN (current provider-scoped OWG SCIM bearer). The Entra token's tid claim must match the confirmed tenant GUID; this is **only a preflight hint**. Microsoft Graph independently authenticates every real request.

Three explicit consent flags are required. Production Graph origin is pinned to https://graph.microsoft.com; the OWG HTTPS host and provider must be confirmed separately. HTTP is permitted only with --allow-loopback-http and a disposable loopback test server. Redirects, ambient proxies, retries, unexpected status/MIME, duplicate JSON keys, oversized responses, stale/revoked provider credentials and mismatched identities are refused. No directory-wide enumeration, full-group enumeration, write, background job, event-log append or credential refresh occurs.

Graph may return additional properties in selected membership responses despite the id projection; those bytes are parsed in memory, never printed or persisted by this tool. Provider access logs may independently record selected resource URLs. The private manifest contains IDs; protect and delete it according to organizational retention policy. Output is **aggregate counts and limitation flags only**, without names, email, IDs, tokens or raw responses.

## Private input manifest

At most 16 KiB UTF-8 JSON, no duplicate keys. The GUIDs below are placeholders, not actual customer mappings. All User and Group mappings must be confirmed by the operator, never inferred from email, names or externalId.

    {
      "format": "openworkgraph.scim-entra-group-reconcile.v1",
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

The selected Group has the first selected User as a member and the second as a nonmember. **It may have other unselected members**. For inactive Users, expected membership must be absent under the currently supported OWG direct-membership contract.

## Operator invocation

    export OWG_ENTRA_READ_TOKEN='AUTHORIZED_MICROSOFT_GRAPH_READ_BEARER'
    export OWG_SCIM_PROBE_TOKEN='CURRENT_OWG_PROVIDER_SCOPED_BEARER'

    python -m scripts.scim_entra_group_reconcile \
      --owg-base-url https://owg.example.test/scim/v2/DISPOSABLE_PROVIDER_ID \
      --confirm-owg-host owg.example.test \
      --confirm-provider DISPOSABLE_PROVIDER_ID \
      --confirm-tenant 11111111-2222-3333-4444-555555555555 \
      --consent-entra-membership-read \
      --consent-owg-membership-read \
      --consent-cross-system-reconcile \
      < /private/selected_entra_owg_groups.json

    unset OWG_ENTRA_READ_TOKEN OWG_SCIM_PROBE_TOKEN

For N selected Users and G selected Groups, **3N+G+5 GETs** (9–33 total) are made. Each request has a 10-second timeout and bounded response size; Graph direct-membership results must fit **one complete page of at most 100 groups**. This intentionally excludes large-membership users until a separately reviewed, bounded pagination implementation exists. No partial success is returned. Success is selected_direct_memberships_match only when all selected mappings agree, with whole_group_parity=not_verified, nested_transitive_memberships=not_verified, snapshot_consistency=not_atomic and customer_idp_provisioning_compatibility=unverified.

## Acceptance and limitations

Committed integration tests exercise disposable Graph-shaped **real TCP HTTP**, actual production OWG FastAPI SCIM routes, and two independent loopback TCP transports, plus direct-member mismatch, unselected Group handling, revoked credentials, Graph rate limits, redirects, malformed/oversized pages, pagination refusal, read-time drift, manifest tampering, consent refusal and redacted receipts. These are synthetic/owned-server tests; **they are not live customer Entra acceptance**. Real Graph permissions, paging, eventual consistency, Entra provisioning client request shapes, tenant configuration, role/ACL inheritance, downstream device/SSO/invitation revocation, retention and mixed-worker rollout remain separately unverified.

Do not merge until exact-head required Ubuntu/macOS/Windows pytest, SCIM, browser/process collaboration, accessibility, scale, starter and packaging/release jobs, reviews/threads and current-main mergeability have all been checked. Never infer that a healthy selected group readback grants an agent business action.
