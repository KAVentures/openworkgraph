# Read-only SCIM cross-provider isolation check

**Status:** optional operator-invoked readiness check; no identity write, no real IdP certification. This extends the [single-provider live SCIM readiness probe](SCIM_READONLY_ROLLOUT_PROBE.md) with a concrete two-provider isolation check against an **actual running OWG SCIM service**. It does not modify the SCIM implementation or relax conditional writes.

## When to use

Before connecting a customer-controlled Entra/Okta sandbox, an administrator can create and enable **two separate disposable provisioning providers** in the existing OWG administrator console. Obtain their two one-view provider-scoped bearer credentials into an approved environment. Both providers must be on the **same explicitly confirmed OWG origin** and must have different provider IDs and credentials. Do not use employee identities or a production provider for a first test.

The tool does not create providers, credentials, users, groups or permissions. The operator supplies and confirms the provider IDs and the OWG hostname; credentials are read only from separate environment variables. Do not paste credentials into command arguments or files.

```sh
export OWG_SCIM_PROBE_TOKEN_A='REPLACE_WITH_FIRST_DISPOSABLE_PROVIDER_TOKEN'
export OWG_SCIM_PROBE_TOKEN_B='REPLACE_WITH_SECOND_DISPOSABLE_PROVIDER_TOKEN'
python -m scripts.scim_provider_isolation_probe \
  --base-url-a https://owg.example.test/scim/v2/PROVIDER_A \
  --base-url-b https://owg.example.test/scim/v2/PROVIDER_B \
  --confirm-host owg.example.test \
  --confirm-provider-a PROVIDER_A \
  --confirm-provider-b PROVIDER_B \
  --consent-cross-provider-read
unset OWG_SCIM_PROBE_TOKEN_A OWG_SCIM_PROBE_TOKEN_B
```

For an operator-owned disposable **local** OWG server only, URLs may begin with `http://127.0.0.1:<port>/scim/v2/<provider_id>` with `--confirm-host 127.0.0.1 --allow-loopback-http`. Remote HTTP is refused. Real HTTPS uses the platform trust store for certificate and hostname validation.

## Exact sequence and safety

The tool performs at most **14 sequential, read-only GET requests**:

1. Five single-provider discovery and `count=0` collection checks using A's own credential against A.
2. The same five checks using B's credential against B.
3. A's credential against B's `ServiceProviderConfig`, requiring an exact **401**.
4. B's credential against A's `ServiceProviderConfig`, also requiring an exact **401**.
5. One last successful `ServiceProviderConfig` check for A and B with their own tokens. This prevents an already-revoked token's cross-provider 401 from being mistaken for isolation.

Each request is bounded to 10 seconds, 64 KiB response maximum for successful reads, no proxy, no redirect, no retry, strict SCIM JSON/no-store for successful reads, and the exact operator-confirmed same origin. No `Users/{id}` or `Groups/{id}` GET, no identity resource download, no SCIM write and no external IdP call. A 403, 404, redirect, 200 on a cross-provider request, same token, same provider, revoked own token or any malformed positive discovery **refuses**. Even a successful receipt never prints provider IDs, URLs, tokens, identity counts or names.

Success returns exit 0 and `cross_provider_read_isolation_passed`, with 14 read requests and zero identity resources/writes. Refusal returns exit 2 with a generic non-secret reason. The server may independently audit reads under its own retention policy.

## What this does not prove

This is an operator-controlled **point-in-time credential isolation smoke test**, not a cryptographic proof that every SCIM route, mutation or tenant is isolated. It does not check cross-tenant writes, SCIM contact-email mappings, missing/stale If-Match, Bulk, Group membership, concurrent administration, offboarding or rehire. No two-provider sandbox result is available until a customer explicitly runs it. The test suite exercises actual production FastAPI SCIM routes with two disposable enabled providers, and actual loopback TCP transport, plus refusal, consent and revocation. Synthetic/local acceptance is distinct from real customer IdP acceptance.

Required exact-head native Ubuntu/macOS/Windows pytest, SCIM, browser, process collaboration, accessibility, scale, starter and packaging/release jobs must all pass before merging. See [SCIM_CLIENT_POLICY.md](SCIM_CLIENT_POLICY.md), [SCIM_USER_PROVISIONING.md](SCIM_USER_PROVISIONING.md) and #458/#412.
