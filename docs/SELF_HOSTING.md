# Self-hosting OpenWorkGraph Gateway

OpenWorkGraph remains local-first. The organization Gateway is optional and can be run entirely inside infrastructure controlled by the customer. No OpenWorkGraph cloud account is required, and OpenWorkGraph/Kinvectum does not need to receive the organization's workflow evidence.

## What runs where

```text
Employee endpoint
  OpenWorkGraph desktop/browser sensors
  privacy hardening
  local SQLite source of truth
  local dashboard/export/MCP
          |
          | optional outbound HTTPS only
          v
Customer infrastructure
  OpenWorkGraph Gateway
  customer PostgreSQL
  REST API / optional MCP adapter
          |
          +--> internal AI
          +--> authorized automation/context integrations
```

The endpoint always writes evidence locally first. Gateway synchronization is a separate worker. If the Gateway, network, or sync worker is unavailable, local capture continues.

## Quick start

Requirements: Docker with Docker Compose.

```bash
git clone https://github.com/KAVentures/openworkgraph.git
cd openworkgraph
cp deploy/.env.example deploy/.env
```

Replace every placeholder in `deploy/.env` with an independently generated secret. For example:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Start PostgreSQL and the Gateway:

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.yml up -d --build
```

By default the Gateway is exposed on port `8790`; PostgreSQL is not published to the host network.

Check it:

```bash
curl http://127.0.0.1:8790/health
```

Production deployments should terminate TLS at a customer-controlled reverse proxy/load balancer and restrict network access according to the organization's security policy. Do not place an unencrypted Gateway directly on the public internet.

## Enroll an endpoint

### Preferred: single-use organization-bound enrollment code

Use the Gateway admin token or enrollment-issuer secret to mint a short-lived code bound to the intended organization and, optionally, actor:

```bash
curl -X POST https://openworkgraph.company.internal/v1/admin/enrollment-codes \
  -H 'Authorization: Bearer <admin-or-enrollment-issuer-token>' \
  -H 'Content-Type: application/json' \
  -d '{
    "organization_id":"acme",
    "actor_id":"alice",
    "expires_minutes":30
  }'
```

Give the returned one-time code to the approved endpoint. From the endpoint installation:

```bash
python -m connector.enroll \
  --gateway https://openworkgraph.company.internal \
  --organization acme \
  --actor alice \
  --enrollment-token '<single-use enrollment code>'
```

The Gateway, not endpoint event JSON, determines the organization/actor/device identity attached to synchronized evidence. A single-use code cannot be reused, expires, and cannot silently replace another active device credential. Re-enrolling an existing active `device_id` returns a conflict until the old device is explicitly revoked or rotated.

For compatibility with existing v0.53 self-hosted deployments, the long-lived `OWG_GATEWAY_ENROLLMENT_TOKEN` can still be supplied directly to the enrollment endpoint. This legacy path is intended as a migration/bootstrap path; single-use organization-bound codes are preferred for normal provisioning.

Enrollment:

1. creates a device-specific write credential;
2. stores the device credential locally (the Gateway stores only its hash);
3. configures `gateway.enabled=true` and the Gateway URL;
4. associates the endpoint with its organization/actor/device identity;
5. sets the synchronization boundary to the current local event ID, so **pre-enrollment history remains local by default**.

At ingestion the Gateway ignores organization/actor/device identity claimed by event JSON and uses the authenticated device identity instead.

The running dashboard can enroll without restarting OpenWorkGraph. CLI enrollment remains available for administrators and scripted setup.

## Pause or resume company sharing

Pausing Gateway sharing does **not** stop local capture:

```bash
python -m connector.control pause
python -m connector.control status
python -m connector.control resume
```

Since v0.53.1, pause has privacy semantics rather than queue semantics:

- evidence captured before the pause can finish synchronizing normally;
- evidence captured while paused stays in the local canonical database;
- that paused interval is recorded as a permanent local-only skip range and is **not backfilled on resume**;
- resuming only shares later eligible evidence.

The endpoint's canonical evidence database remains local throughout.

## Sharing policy

Two policies are combined before transmission:

- the endpoint-local policy;
- the organization policy returned by the Gateway.

The merge is restrictive: organization policy can narrow what is shared but cannot broaden a restriction set locally on the endpoint. If both sides specify non-empty event-type allowlists and their intersection is empty, synchronization shares **no event types**; an empty intersection never means “allow everything.”

Examples of controls include:

- excluded events are not shared by default;
- window titles can be stripped;
- all metadata can be stripped;
- event types can be allow-listed;
- selected metadata keys can be recursively removed.

Policy is enforced **before transmission**. If the current organization policy cannot be fetched, synchronization fails closed rather than uploading with a potentially broader fallback policy.

Typed text, ordinary key identities, clipboard contents, and screenshot bytes are outside the Gateway contract. The Gateway rejects payloads that claim to contain those categories.

## Synchronization failure handling

Network, authentication, and server failures remain retryable and do not advance the local synchronization cursor.

A terminally invalid event (for example an event rejected as oversized or violating the Gateway privacy contract) is isolated rather than blocking every later event forever. Good rows in the surrounding batch continue, while the bad local row is placed in the endpoint's synchronization quarantine with its reason. The dashboard exposes the quarantine count. Quarantine never deletes the local canonical evidence.

## Time handling

Gateway ingest and query boundaries accept timezone-aware ISO-8601 timestamps such as `2026-09-23T12:00:00+02:00` or `2026-09-23T10:00:00Z`. They are parsed and normalized to a fixed UTC representation before storage/comparison. Invalid or timezone-naive timestamps are rejected rather than being compared as arbitrary text.

## Create a REST integration credential

The admin bootstrap token is not an integration token. Create a scoped service token for each integration:

```bash
curl -X POST https://openworkgraph.company.internal/v1/admin/integration-tokens \
  -H 'Authorization: Bearer <admin token>' \
  -H 'Content-Type: application/json' \
  -d '{
    "organization_id":"acme",
    "label":"Automation integration",
    "scopes":["evidence:read","context:read","transfers:read"]
  }'
```

The returned token is shown once. Store it in the integration's secret store. The Gateway stores its hash and supports revocation by token ID.

Current read scopes:

- `evidence:read` — chronological evidence trace and search;
- `context:read` — recent observed context;
- `transfers:read` — copy/cut/paste transfer linkage, never clipboard contents.

Device credentials cannot read organization evidence, and integration credentials cannot ingest endpoint evidence.

## Core REST interface

```text
GET  /v1/capabilities
GET  /v1/workflow-trace
POST /v1/search
GET  /v1/context/current
GET  /v1/transfers
GET  /v1/admin/retention/{organization_id}
PUT  /v1/admin/retention/{organization_id}
```

`/v1/workflow-trace` is the canonical organization evidence interface. Results are chronological, bounded, and cursor-paginated. Each row retains the privacy-hardened event metadata plus its `event_id`, while also exposing useful flat indexes such as page/target, timing, tab context, semantic-action hints, and transfer IDs.

Invalid cursors and invalid timestamp bounds return a client error instead of an internal-server error. `/v1/transfers` searches transfer-bearing evidence rather than consuming its result limit on unrelated focus/click rows.

OpenWorkGraph does not require a deterministic task label before an AI can inspect this evidence.

## Retention and evidence lifecycle

v0.54 adds **opt-in** organization retention for synchronized Gateway evidence. Existing installations remain unchanged unless an administrator configures a retention period.

Setting retention changes what the Gateway can return immediately, but does not silently delete database rows. Physical cleanup is a separate dry-run-first operation that can be scheduled in customer-controlled infrastructure.

Example:

```bash
curl -X PUT https://openworkgraph.company.internal/v1/admin/retention/acme \
  -H 'Authorization: Bearer <admin token>' \
  -H 'Content-Type: application/json' \
  -d '{"retention_days":30}'

python -m gateway.lifecycle apply-retention --organization acme
python -m gateway.lifecycle apply-retention \
  --organization acme \
  --execute \
  --confirm 'APPLY acme'
```

Retroactive Gateway purge supports organization-scoped time, actor, device, session, and event-type selectors. It never reaches back into the endpoint's local SQLite source of truth. See [Gateway data lifecycle](DATA_LIFECYCLE.md) for the full safety model, purge examples, audit behavior, and backup caveats.

## Data ownership and storage

In the self-hosted deployment:

- endpoint evidence begins in the employee/device local OpenWorkGraph store;
- synchronized evidence is stored in the customer's PostgreSQL instance;
- customer-controlled integrations query the customer's Gateway;
- no workflow evidence has to transit or be stored in infrastructure operated by OpenWorkGraph/Kinvectum.

The customer is responsible for production database backups, encryption, retention choices, access controls, TLS, identity-provider integration, and applicable legal/compliance requirements for its deployment. Live-row deletion does not by itself remove customer-managed backups, snapshots, replicas, exports, or downstream copies.

## Development mode

For automated tests and local development the Gateway also supports a SQLite URL. PostgreSQL is the production self-hosting target.

## Enterprise identity

v0.54 uses explicit device credentials, scoped service credentials, and preferred single-use enrollment grants so the data-plane boundary is testable without requiring a vendor cloud account. In larger deployments the Gateway can be placed behind the customer's OIDC/OAuth-aware reverse proxy/identity layer. Native enterprise SSO/group provisioning can be layered on without changing the core self-hosted data plane.
