# Read-only live SCIM provider rollout probe

This operator tool checks the **actual running OWG SCIM HTTP service** before a customer-controlled Entra/Okta provisioning pilot. It complements the offline declared mapping preflight; it does not create, deactivate, delete or modify a User, Group, provider or any OWG permission. It cannot certify a real IdP's request shapes, mapping, lifecycle, concurrency, conditional writes or company rollout.

## Prerequisites

In the existing administrator console, create a **dedicated test provisioning provider**, explicitly enable it, and retain its one-view provider-scoped bearer credential in an approved secret environment. Use a disposable company/test environment; do not use a real employee identity for this probe. The credential is never a generic administrator token. This tool does not create, enable or rotate credentials.

From the repository root, after confirming the exact OWG HTTPS hostname and provider URL:

```sh
export OWG_SCIM_PROBE_TOKEN='REPLACE_WITH_CURRENT_TEST_PROVIDER_TOKEN'
python -m scripts.scim_readonly_rollout_probe \
  --base-url https://owg.example.test/scim/v2/REPLACE_WITH_PROVIDER_ID \
  --confirm-host owg.example.test \
  --consent-readonly-probe
unset OWG_SCIM_PROBE_TOKEN
```

For a **locally owned disposable** HTTP server only, use a URL beginning `http://127.0.0.1:<port>/scim/v2/<provider_id>` with `--confirm-host 127.0.0.1 --allow-loopback-http`. Non-loopback HTTP is refused, even with the flag. Real HTTPS uses system certificate/hostname verification.

## What actually happens

The CLI makes exactly five sequential GET requests under the supplied, explicitly enabled provider credential:

1. `ServiceProviderConfig`: checks the bounded patch/etag/filter/bulk and bearer discovery contract.
2. `ResourceTypes` and `Schemas`: checks advertised User/Group resources, endpoints and core schema identifiers agree.
3. `Users?startIndex=1&count=0` and `Groups?startIndex=1&count=0`: checks authenticated collection reads and SCIM ListResponse pagination without asking for any roster User/Group resource.

The probe uses an exact operator-confirmed host and provider-scoped base path, no redirects, no HTTP proxies, 10-second timeout per request, 64 KiB response limit, strict SCIM JSON content type, `Cache-Control: no-store`, duplicate-key rejection and fail-closed response checks. A 401, revoked/disabled credential, TLS failure, malformed response, unexpected redirect or wrong endpoint produces a generic refusal. There is **no automatic retry**. Even when successful, no identity data, roster counts, token, provider ID or source URL is included in the output. The live server may independently log authenticated reads according to its own audit/retention policy.

The success receipt is `read_only_checks_passed`, **not** `IdP_certified`. The output records five checked categories, zero identity resources requested and zero provider writes. A refusal returns exit code 2; successful checks return 0. No background schedule or cloud upload is started.

## Rollout boundary and acceptance

The production probe can be executed against a customer-authorized OWG endpoint; tests cover both the **actual production FastAPI SCIM router** with an enabled disposable provider and an actual local TCP HTTP server, plus revocation, hostile responses, redirects, consent and wrong-host refusal. These are not tests of a real customer Entra/Okta tenant. Exact-head Ubuntu/macOS/Windows, browser, SCIM, collaboration, scale, accessibility, starter and packaging/release CI remain required before merging.

After this read-only check, a separate administrator-approved, disposable **real IdP sandbox** must exercise create, search, current/stale/missing If-Match, atomic PATCH, deactivate, offboarding/revocation, rehire, Group membership, provider/tenant isolation, restore and mixed-worker upgrade behavior. Do not infer permission to perform any identity writes from this tool or a clean mapping preflight. See [SCIM_USER_PROVISIONING.md](SCIM_USER_PROVISIONING.md), [SCIM_CLIENT_POLICY.md](SCIM_CLIENT_POLICY.md), [SCIM_IDP_MAPPING_PREFLIGHT.md](SCIM_IDP_MAPPING_PREFLIGHT.md) and #458/#412.
