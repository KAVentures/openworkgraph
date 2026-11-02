# Selected SCIM Group membership readback (read-only)

**Scope:** #458 / #412, #326 Section B / #264 Stage 3. This is an operator-initiated point-in-time check against a running, authorized OWG SCIM endpoint, not a provisioning client or live Okta/Entra certification. **SCIM Groups are provider-owned metadata; they never grant OWG roles, teams, process ACLs, SSO or device access.**

## Purpose and prerequisites

After an authorized administrator or customer-controlled test IdP separately provisions **disposable** Users and Groups, confirm that selected real SCIM Groups expose the **exact expected direct User member sets** and that the selected Users' read-only `groups` projections agree. The existing User lifecycle probe checks active states; this one additionally checks direct membership and its reciprocal view. It cannot prove the IdP sent the intended write, that any unselected Group is complete, or that access was revoked.

Use a current provider-scoped bearer credential only through `OWG_SCIM_PROBE_TOKEN`. Confirm the actual HTTPS host and provider ID independently. Explicitly consent to reading the selected Users and Groups. Do not put credentials, IDs, names or emails on the command line. Do not run against a customer tenant without authorization.

## Private input manifest

Store a private, access-controlled UTF-8 JSON file with **1–4 Groups** and **1–8 unique Users**. Every Group's expected `members` list is exact (including an empty list), contains only selected Users, and cannot include an expected-inactive User. The probe reads all selected Users, including Users expected to be absent from all selected Groups. Only opaque IDs, never names/emails/tokens or full SCIM resources, belong in this file.

```json
{
  "format": "openworkgraph.scim-group-membership-readback.v1",
  "provider_id": "DISPOSABLE_PROVIDER_ID",
  "users": [
    {"id": "DISPOSABLE_USER_1", "expected_active": true},
    {"id": "DISPOSABLE_USER_2", "expected_active": true}
  ],
  "groups": [
    {"id": "grp_0123456789abcdef0123456789abcdef", "members": ["DISPOSABLE_USER_1"]}
  ]
}
```

```sh
export OWG_SCIM_PROBE_TOKEN='REPLACE_WITH_CURRENT_DISPOSABLE_PROVIDER_TOKEN'
python -m scripts.scim_group_membership_readback_probe \
  --base-url https://owg.example.test/scim/v2/DISPOSABLE_PROVIDER_ID \
  --confirm-host owg.example.test \
  --confirm-provider DISPOSABLE_PROVIDER_ID \
  --consent-membership-read < /private/selected_scim_memberships.json
unset OWG_SCIM_PROBE_TOKEN
```

For disposable local HTTP only, add `--allow-loopback-http` and use `http://127.0.0.1:<port>/scim/v2/<provider>` with `--confirm-host 127.0.0.1`. Other hosts require HTTPS and normal TLS validation.

## Verification and limitations

The command rejects unsafe/oversized/duplicate-key manifests before contacting the provider. It uses exact-origin, no-proxy, no-redirect, no-retry HTTP GETs with a ten-second timeout and 64 KiB per-response cap. It performs one authenticated `ServiceProviderConfig` GET, one exact `Groups/{id}` GET per selected Group, one exact `Users/{id}` GET per selected User, and a final authenticated provider recheck: **4–14 GETs**. No collection-wide roster enumeration, SCIM write or external IdP request is performed.

Each Group must have the correct schema, ID, version, and exact distinct direct User members. Each User must have the correct schema, ID, version and expected boolean active state; its `groups` projection must agree for **all selected Groups**. Other unselected Group memberships may exist and are not audited. Deleted/foreign IDs, unexpected or missing members, a change between Group and User reads, revoked credentials, invalid JSON, redirects and missing consent cause refusal. Concurrent changes after the last read remain possible: this is **not an atomic snapshot**.

The receipt contains only aggregate checked counts, number of GETs, and explicit `not_verified` limitations. It never prints selected IDs, names, emails or credentials. The OWG server may retain resource paths in its own access logs under its retention policy; the probe itself does not persist the input or response. The operator must handle the private manifest according to local retention rules.

Automated acceptance uses actual OWG FastAPI SCIM endpoints, disposable Users and Groups, reciprocal membership changes, provider revocation, and real loopback TCP. Synthetic/disposable route acceptance is **not** live customer IdP acceptance. Native Ubuntu/macOS/Windows pytest, SCIM contract, browser, accessibility, collaboration, scale, starter and packaging/release gates must all pass on the exact PR head before merge.
