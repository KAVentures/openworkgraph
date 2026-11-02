# Live, read-only SCIM lifecycle readback

**Status:** operator-invoked point-in-time acceptance probe for an owned running OWG SCIM provider. This is not a provisioning client, a write test, proof of full offboarding, or customer IdP certification. Tracks [#458](https://github.com/KAVentures/openworkgraph/issues/458) / [#412](https://github.com/KAVentures/openworkgraph/issues/412).

## Purpose

After an authorized administrator or customer-controlled test IdP has separately provisioned or deactivated selected **disposable** test Users, check whether the running OWG SCIM endpoint actually reports each selected User's expected `active` state. This closes a gap between static IdP mapping review and the existing discovery-only readiness check: the new probe reads selected real resource state instead of inferring that a lifecycle write worked from configuration or mock responses.

The operator must already possess the provider's current bearer credential and the opaque SCIM resource IDs. The tool does **not** create users, request deactivation, read device sessions, revoke tokens, alter roster records, or call an external IdP. It cannot establish that SSO/device/invitation access was revoked, that an IdP transmitted the expected mutation, or that the provider's data is complete.

## Run with explicit consent

Create a private, access-controlled local JSON file containing **only** 1–8 selected opaque SCIM User IDs and expected boolean states. Never include emails, names, tokens, passwords, or raw User records. Example placeholders:

```json
{
  "format": "openworkgraph.scim-lifecycle-readback-probe.v1",
  "provider_id": "DISPOSABLE_PROVIDER_ID",
  "users": [
    {"id": "DISPOSABLE_SCIM_USER_ID_1", "expected_active": true},
    {"id": "DISPOSABLE_SCIM_USER_ID_2", "expected_active": false}
  ]
}
```

```sh
export OWG_SCIM_PROBE_TOKEN='REPLACE_WITH_CURRENT_DISPOSABLE_PROVIDER_TOKEN'
python -m scripts.scim_lifecycle_readback_probe \
  --base-url https://owg.example.test/scim/v2/DISPOSABLE_PROVIDER_ID \
  --confirm-host owg.example.test \
  --confirm-provider DISPOSABLE_PROVIDER_ID \
  --consent-user-state-read < /private/selected_scim_states.json
unset OWG_SCIM_PROBE_TOKEN
```

Use an operator-owned local test server with `--allow-loopback-http --confirm-host 127.0.0.1` only for `http://127.0.0.1:<port>/scim/v2/<provider>`. All non-loopback requests require HTTPS with platform TLS validation. Do not put credentials or User IDs in CLI arguments or shell history. Input comes from bounded stdin; the tool does not save the manifest, response body, or receipt.

## Verification and privacy contract

The probe validates the exact confirmed provider path and a manifest of at most eight unique ASCII-safe opaque IDs before contacting the network. It uses an environment-only credential and no-proxy, no-redirect, no-retry transport, with a ten-second timeout and 64 KiB maximum response per GET. It performs one authenticated `ServiceProviderConfig` GET, one exact `Users/{id}` GET per selected resource, and a final authenticated `ServiceProviderConfig` recheck: **3–10 GETs** total. Every positive response must be strict SCIM JSON, `no-store`, exact URL, and a valid schema. Each User must have exactly the requested ID, boolean `active` matching the manifest, and a nonempty versioned User metadata envelope. Missing/foreign/deleted resources, state mismatch, invalid/oversized JSON, revoked tokens, redirects, wrong host or missing consent fail closed.

The success receipt reports only `selected_user_states_verified`, count, number of GETs and explicit `not_verified` limitations. Failure is a generic refusal, without User IDs, names, emails, provider IDs or tokens. The actual OWG server may retain access logs containing selected resource paths according to its own retention policy. A provider can change state immediately after the last GET; this is **point-in-time readback**, not an atomic snapshot or continuous enforcement.

## Acceptance boundaries

Automated regressions use the production OWG FastAPI SCIM routes with disposable providers and Users, actual User PATCH deactivation, a real local TCP transport, state mismatch, missing resource, revocation between reads, CLI consent, manifest rejection and no-identity-echo assertions. Synthetic and disposable local success is not real customer-controlled Okta/Entra acceptance. Native Ubuntu/macOS/Windows pytest, SCIM contract, browser/accessibility, collaboration, scale, starter, packaging and release gates must all pass at the exact PR head before merge. Do not use this probe against a real customer tenant without authorization.
