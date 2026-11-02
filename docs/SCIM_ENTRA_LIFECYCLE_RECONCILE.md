# Read-only Microsoft Entra ↔ OWG SCIM lifecycle reconciliation

This is an **operator-invoked, bounded, live two-system read**, not a provisioning connector or automatic sync. It advances [#458](https://github.com/KAVentures/openworkgraph/issues/458), [#412](https://github.com/KAVentures/openworkgraph/issues/412) and [#326 B](https://github.com/KAVentures/openworkgraph/issues/326) by allowing an authorized administrator to verify whether **selected Microsoft Entra accounts** and **selected OWG SCIM Users** currently report the same explicitly expected active/deactivated state. It is useful after a customer-controlled Entra provisioning test, including a deliberate test-user deactivation. It does **not** establish that Entra sent a SCIM request, that the OWG server accepted that request correctly, or that device/SSO/invitation access was revoked.

## Authority and privacy

The operator must have explicit permission to read the selected Entra and OWG identities. Use **two independent, current, read-only credentials** in environment variables: `OWG_ENTRA_READ_TOKEN` (Microsoft Graph bearer token authorized to read selected Users; use least privilege such as `User.Read.All` where supported) and `OWG_SCIM_PROBE_TOKEN` (the current OWG provider-scoped SCIM bearer). No credential acquisition, OAuth client secret, customer tenant or IdP action is performed by the tool. An Entra account with appropriate rights and an existing OWG provider must already be configured.

Microsoft's [Get user API](https://learn.microsoft.com/en-us/graph/api/user-get?view=graph-rest-1.0) documents the `GET /users/{id}` operation and `$select` for properties not returned by default. The tool requests **only** `id,accountEnabled` for each selected Entra GUID. It never enumerates the directory, requests email/name fields, reads groups or writes to Graph. An explicit tenant GUID is compared with the token's `tid` claim **as a preflight hint only**: the JWT is not locally signature-verified; Microsoft Graph independently validates its bearer on every real request. The claim does not grant authority. The production Graph origin is fixed to `https://graph.microsoft.com`; sovereign cloud and custom endpoints are not supported by this milestone.

OWG uses the existing provider-scoped `/scim/v2/{provider}/ServiceProviderConfig` and selected `/Users/{id}` routes. SCIM User `active` is a provisioning lifecycle field, not a proof of downstream access revocation. The tool does not infer a link between Entra and OWG identities from email, display name or externalId: the operator supplies each **explicit mapping**. Never treat matching lifecycle states as verified identity equivalence, workflow permission, or business outcome.

## Private manifest

Prepare an access-controlled local UTF-8 JSON file, at most 16 KiB, with **1–8 distinct exact Entra GUID → OWG SCIM User ID mappings**, and an independently known expected active state for each. All IDs and expected states are operator-confirmed, not inferred. The provider and tenant must match the separately confirmed command arguments.

```json
{
  "format": "openworkgraph.scim-entra-lifecycle-reconcile.v1",
  "provider_id": "DISPOSABLE_PROVIDER_ID",
  "entra_tenant_id": "11111111-2222-3333-4444-555555555555",
  "users": [
    {
      "entra_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
      "owg_id": "DISPOSABLE_OWG_USER_ID",
      "expected_active": false
    }
  ]
}
```

```sh
# Use already-authorized short-lived read credentials; never put tokens on the CLI.
export OWG_ENTRA_READ_TOKEN='REPLACE_WITH_AUTHORIZED_GRAPH_READ_BEARER'
export OWG_SCIM_PROBE_TOKEN='REPLACE_WITH_CURRENT_OWG_PROVIDER_BEARER'

python -m scripts.scim_entra_lifecycle_reconcile \
  --owg-base-url https://owg.example.test/scim/v2/DISPOSABLE_PROVIDER_ID \
  --confirm-owg-host owg.example.test \
  --confirm-provider DISPOSABLE_PROVIDER_ID \
  --confirm-tenant 11111111-2222-3333-4444-555555555555 \
  --consent-entra-read \
  --consent-owg-read \
  --consent-cross-system-reconcile \
  < /private/selected_entra_owg_lifecycle.json

unset OWG_ENTRA_READ_TOKEN OWG_SCIM_PROBE_TOKEN
```

The default Graph origin is fixed; do not pass `--graph-origin` for production. `--allow-loopback-http` permits **only explicit disposable local HTTP** tests (both Graph-shaped test server and OWG local server); it is not a production security bypass. Never run the test server against customer data. The selected manifest should be owner-readable only and deleted according to your organization's retention policy.

## Bounded behavior and acceptance

Preflight checks all three consents, exact OWG host/provider, Entra tenant GUID and token tenant hint, two current credential shapes, bounded/duplicate-free manifest, and production/loopback URL rules **before any network request**. The runtime performs one authenticated OWG ServiceProviderConfig GET, then one real Microsoft Graph GET and one real OWG SCIM User GET for each mapping, then rechecks the first Entra User and OWG provider authority. Total: **2N+3 GETs (5–19 for 1–8 mappings)**, each with a 10-second timeout, no ambient proxy, no redirects, no retry, bounded JSON (64 KiB), and exact selected ID/schema/version/state validation. Any mismatch, HTTP 401/404/429, invalid/oversized/duplicate JSON, missing state or revocation refuses the entire check; no partial-success receipt is issued.

Success prints only `status=selected_lifecycle_states_match`, number of selected users, request count, and explicit limitation flags. Refusal prints only a generic non-identifying reason. Neither output contains user IDs, tenant/provider IDs, names, email, token, resource payload or source paths. The source and OWG HTTP servers may independently retain selected resource URLs in their access logs; this tool does not persist request/response bodies. No local OWG event-log append, provisioning write, automatic polling, background job, credential refresh or permission change occurs.

**Important limits:** The two providers are read sequentially, not in one transaction. Concurrent state changes after readback can escape detection; final rechecks are bounded and not an atomic snapshot. The tool verifies selected lifecycle projections only, not the entire tenant, cross-tenant directory membership, source event history, SCIM request-shape compatibility, device revocation, SSO token invalidation, invitations, or the customer's IdP configuration. Customer-controlled real Entra and OWG acceptance must be run separately and recorded with permission. Disposable Graph-shaped HTTP + actual OWG FastAPI/SCIM routes and real TCP acceptance tests are **synthetic integration evidence**, not a substitute for that acceptance.

Do not merge this feature without exact-head Ubuntu/macOS/Windows pytest, SCIM, browser, process collaboration, accessibility, scale, starter and packaging/release checks and review/current-main compatibility. No existing SCIM contact-email, roster, source-connector or Jira behavior is changed.
