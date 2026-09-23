# Human access, OIDC and privacy-scoped reads

OpenWorkGraph Gateway supports two deliberately separate access models:

1. **Machine/service access** — existing device and integration tokens. These remain backward-compatible.
2. **Human access** — optional OIDC/SSO tokens whose effective permissions are derived from verified identity claims and configured IdP group mappings.

OIDC is disabled by default. Leaving the OIDC environment variables empty preserves the existing service-token behavior.

## Required OIDC configuration

Set all three together:

- `OWG_GATEWAY_OIDC_ISSUER`
- `OWG_GATEWAY_OIDC_AUDIENCE`
- `OWG_GATEWAY_OIDC_JWKS_URL`

JWT signatures are verified against the configured JWKS. The Gateway accepts asymmetric RSA/ECDSA signing algorithms only and validates issuer, audience, expiry and subject.

Identity claim names are configurable:

- `OWG_GATEWAY_OIDC_ORGANIZATION_CLAIM` — default `owg_org`
- `OWG_GATEWAY_OIDC_ACTOR_CLAIM` — default `sub`
- `OWG_GATEWAY_OIDC_GROUPS_CLAIM` — default `groups`

The organization and actor claims must map to the identifiers used when endpoint evidence is enrolled. Do not use mutable display names as stable actor identifiers.

## Effective human scopes

An authenticated human receives `self:evidence:read` by default. This can be disabled with `OWG_GATEWAY_OIDC_SELF_READ=false`.

Additional permissions come only from `OWG_GATEWAY_OIDC_GROUP_SCOPE_MAP`, a JSON map from exact IdP group names to scopes.

Example:

```json
{
  "Engineering Managers": ["team:engineering:evidence:read"],
  "Internal Audit": ["org:evidence:read"],
  "Process Analysts": ["aggregate:read"],
  "Restricted Research": ["pseudonymous:evidence:read"]
}
```

Supported scopes:

- `self:evidence:read` — own raw privacy-hardened evidence only.
- `team:<team-id>:evidence:read` — raw evidence for actors assigned to that team.
- `org:evidence:read` — raw evidence for any actor in the organization. This is intentionally explicit and should be narrowly assigned.
- `aggregate:read` — thresholded organization patterns only; no actor identifiers are returned.
- `pseudonymous:evidence:read` — organization evidence with actor/device/session identifiers replaced by stable HMAC pseudonyms. This is a separate permission and is **not** a claim of anonymization.

Human raw-trace requests default to the caller's own actor even when the caller has a broader scope. Reading another actor requires an explicit `actor_id` parameter and the corresponding team or organization permission.

## Team membership

Team membership is server-side authorization state. An IdP group can grant a team scope, while the Gateway separately records which actors belong to that team.

Admin endpoints:

```text
PUT /v1/admin/access/{organization_id}/actors/{actor_id}/teams
GET /v1/admin/access/{organization_id}/actors/{actor_id}/teams
```

Example request body:

```json
{"teams": ["engineering"]}
```

An actor must be assigned to `engineering` before a principal with `team:engineering:evidence:read` can access that actor's trace.

## Aggregate-only access

`GET /v1/human/aggregate/patterns` returns privacy-reduced organization patterns grouped by event type and application.

To reduce differencing attacks, v0.56.1 no longer evaluates arbitrary timestamp windows literally. Requested `since`/`until` bounds are reduced to **complete UTC ISO weeks fully contained inside the requested interval**. The current partial week is never returned. Organization retention is applied before the window is snapped, so bucketing can never broaden retention.

Privacy properties:

- actor IDs are never returned;
- exact cohort size is not returned;
- the entire response is suppressed when fewer than `k` distinct actors contributed;
- every returned pattern must itself have contributions from at least `k` actors;
- the configured minimum cannot be lowered by a caller;
- event/actor counts are coarsened and durations are rounded;
- organization retention policy still applies;
- responses report their effective bucketed time boundaries so consumers do not mistake them for the exact requested timestamps.

`OWG_GATEWAY_AGGREGATE_MIN_ACTORS` defaults to `5` and cannot be configured below `3`. For employee deployments, a higher threshold is preferable where cohort size permits it.

Thresholding, fixed windows and rounding reduce disclosure risk. They are not formal differential privacy and OpenWorkGraph does not describe these results as anonymous. Repeated-query attacks are still part of the deployment threat model; especially sensitive deployments should additionally restrict who receives `aggregate:read` and consider a query budget or differential-privacy layer.

## Pseudonymous access

`GET /v1/human/pseudonymous/workflow-trace` requires `pseudonymous:evidence:read` and a stable `OWG_GATEWAY_PSEUDONYM_KEY` of at least 32 characters.

Actor, device and session identifiers are replaced with deterministic organization-scoped HMAC pseudonyms. Rotating the key intentionally changes those pseudonyms.

The endpoint uses the same opaque cursor pagination model as the normal workflow trace. Supply the returned `next_cursor` as `cursor` to retrieve the next page; page boundaries do not expose raw actor/device/session identifiers.

Pseudonymized workflow evidence can still be personal data because application/window/page context may identify a person indirectly. Use the aggregate-only scope when individual traces are unnecessary.

## Audit behavior

Human reads are written to the Gateway audit log. The raw OIDC subject is not stored in the audit principal ID; it is represented by a one-way issuer+subject fingerprint. Audit entries record the access mode and returned-row counts.

## Service-token compatibility

Existing integration-token endpoints and scopes are unchanged. Human OIDC JWTs are accepted only by `/v1/human/*` routes. Existing Gateway integration tokens continue to use the existing machine REST/MCP routes.

For new enterprise deployments, prefer human OIDC for interactive access and narrowly scoped service tokens for machine integrations. Avoid issuing broad organization-wide integration tokens unless the integration genuinely needs individual-level evidence.
