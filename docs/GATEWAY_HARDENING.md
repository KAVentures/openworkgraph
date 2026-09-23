# Gateway operational hardening

OpenWorkGraph v0.55 adds optional operational controls around the existing self-hosted Gateway without changing the evidence model, token format, local capture path, or organization lifecycle semantics.

The hardened production entrypoint is `gateway.enterprise_app:app`. The Docker image uses it automatically. The existing `gateway.app:create_app` remains the stable core application factory so tests and downstream code that compose the core Gateway are not forced onto the optional operational layer.

## Compatibility defaults

All controls that could alter request throughput or PostgreSQL connection behavior are disabled unless configured:

- `OWG_GATEWAY_DB_POOL_MAX_SIZE=0` means direct PostgreSQL connections, matching earlier releases;
- `OWG_GATEWAY_PRINCIPAL_RATE_LIMIT_PER_MINUTE=0` disables the principal bucket;
- `OWG_GATEWAY_ADMIN_RATE_LIMIT_PER_MINUTE=0` disables the admin bucket;
- `OWG_GATEWAY_ENROLLMENT_RATE_LIMIT_PER_MINUTE=0` disables the enrollment bucket.

The supplied `deploy/.env.example` contains conservative recommended values for new self-hosted installs. Existing deployments that do not add the variables keep the old behavior.

## PostgreSQL connection pooling

Enable the built-in psycopg pool with:

```text
OWG_GATEWAY_DB_POOL_MIN_SIZE=1
OWG_GATEWAY_DB_POOL_MAX_SIZE=10
OWG_GATEWAY_DB_POOL_TIMEOUT_SECONDS=10
```

Pooling is PostgreSQL-only. SQLite development mode ignores pool sizing even if values are supplied.

The pool is opened lazily, reused by the existing `GatewayDB` interface, and closed on Gateway shutdown. No evidence/query code needs a separate pooled code path.

For very large deployments, size the pool together with PostgreSQL `max_connections`, the number of Gateway replicas, expected integration concurrency, and any connection pooler already operated by the customer.

## Rate limiting

The built-in limiter is a per-process sliding-window overload guard keyed by a one-way SHA-256 fingerprint of the presented bearer credential. Raw bearer values are not retained by the limiter.

Example:

```text
OWG_GATEWAY_PRINCIPAL_RATE_LIMIT_PER_MINUTE=600
OWG_GATEWAY_ADMIN_RATE_LIMIT_PER_MINUTE=120
OWG_GATEWAY_ENROLLMENT_RATE_LIMIT_PER_MINUTE=60
```

A value of `0` disables that bucket. When a bucket is exhausted the Gateway returns HTTP `429` and a `Retry-After` header.

Health, capability, OpenAPI and documentation probes are excluded so load-balancer health checks do not consume credential quotas.

This is intentionally **not** represented as a distributed DDoS or brute-force control. Each Gateway process has its own limiter state. Multi-replica deployments should enforce aggregate limits, request-size limits, TLS and network/WAF policy at the customer-controlled reverse proxy or load balancer as well.

## Credential and device inventory

The admin API can list credential metadata for an organization:

```text
GET /v1/admin/tokens/{organization_id}
GET /v1/admin/devices/{organization_id}
```

Optional token query parameters:

- `token_type=device|integration`
- `include_revoked=true|false`
- `limit=...`

Inventory rows contain only non-secret metadata already stored in the Gateway token table, such as:

- token ID;
- credential type;
- organization / actor / device identity;
- scopes;
- creation time;
- revocation time;
- active/revoked status.

The admin inventory **never returns the bearer token or stored token hash**.

## Administrative device revocation

An administrator can revoke the active device credential(s) for one organization/device pair:

```text
DELETE /v1/admin/devices/{organization_id}/{device_id}
```

This revokes authentication only. It does **not** delete synchronized Gateway evidence and does not send any remote deletion command to endpoint-local OpenWorkGraph data. Evidence deletion remains a separate lifecycle operation documented in `DATA_LIFECYCLE.md`.

Administrative inventory reads and device revocations are written to the existing Gateway audit log.

## Runtime visibility

Administrators can inspect non-secret operational configuration:

```text
GET /v1/admin/runtime
```

The response reports whether PostgreSQL pooling is active, configured pool sizes, configured rate limits, storage mode and version. It does not return database credentials, admin/enrollment tokens, integration bearer tokens or token hashes.

## Trust boundary

These controls strengthen the self-hosted Gateway but do not turn it into a full enterprise identity provider or perimeter security product. Production customers should still place the Gateway behind their own TLS termination, network controls, SSO/OIDC-aware access layer where appropriate, monitoring, backup policy and centralized edge-rate controls.
