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
          +--> Akai/Codos/other authorized integrations
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

The organization administrator supplies the Gateway URL, organization identifier, and short-lived/bootstrap enrollment secret to an approved endpoint administrator.

From the OpenWorkGraph endpoint installation:

```bash
python -m connector.enroll \
  --gateway https://openworkgraph.company.internal \
  --organization acme \
  --actor alice \
  --enrollment-token '<enrollment secret>'
```

Enrollment:

1. creates/replaces a device-specific write credential;
2. stores it locally (not in the Gateway database as plaintext);
3. configures `gateway.enabled=true` and the Gateway URL;
4. associates the endpoint with its organization/actor/device identity.

The Gateway stores only a hash of the device token. At ingestion it ignores organization/actor/device identity claimed by event JSON and uses the authenticated device identity instead.

Restart OpenWorkGraph after enrollment. The normal launcher starts the Gateway connector as an independent process only when the endpoint is explicitly configured and enrolled.

## Pause or resume company sharing

Pausing Gateway sharing does **not** stop local capture:

```bash
python -m connector.control pause
python -m connector.control status
python -m connector.control resume
```

The endpoint's canonical evidence database remains local throughout.

## Sharing policy

Two policies are combined before transmission:

- the endpoint-local policy;
- the organization policy returned by the Gateway.

The merge is restrictive: organization policy can narrow what is shared but cannot broaden a restriction set locally on the endpoint.

Examples of controls include:

- excluded events are not shared by default;
- window titles can be stripped;
- all metadata can be stripped;
- event types can be allow-listed;
- selected metadata keys can be recursively removed.

Policy is enforced **before transmission**. If the current organization policy cannot be fetched, synchronization fails closed rather than uploading with a potentially broader fallback policy.

Typed text, ordinary key identities, clipboard contents, and screenshot bytes are outside the Gateway contract. The Gateway rejects payloads that claim to contain those categories.

## Create a REST integration credential

The admin bootstrap token is not an integration token. Create a scoped service token for each integration:

```bash
curl -X POST https://openworkgraph.company.internal/v1/admin/integration-tokens \
  -H 'Authorization: Bearer <admin token>' \
  -H 'Content-Type: application/json' \
  -d '{
    "organization_id":"acme",
    "label":"Akai integration",
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
```

`/v1/workflow-trace` is the canonical organization evidence interface. Results are chronological, bounded, and cursor-paginated. Each row retains the privacy-hardened event metadata plus its `event_id`, while also exposing useful flat indexes such as page/target, timing, tab context, semantic-action hints, and transfer IDs.

OpenWorkGraph does not require a deterministic task label before an AI can inspect this evidence.

## Data ownership and storage

In the self-hosted deployment:

- endpoint evidence begins in the employee/device local OpenWorkGraph store;
- synchronized evidence is stored in the customer's PostgreSQL instance;
- customer-controlled integrations query the customer's Gateway;
- no workflow evidence has to transit or be stored in infrastructure operated by OpenWorkGraph/Kinvectum.

The customer is responsible for production database backups, encryption, retention, access controls, TLS, identity-provider integration, and applicable legal/compliance requirements for its deployment.

## Development mode

For automated tests and local development the Gateway also supports a SQLite URL. PostgreSQL is the production self-hosting target.

## Enterprise identity

v0.53 uses explicit device and scoped service credentials so the data-plane boundary is testable without requiring a vendor cloud account. In larger deployments the Gateway can be placed behind the customer's OIDC/OAuth-aware reverse proxy/identity layer. Native Entra/Okta/SCIM/MDM provisioning is a later enterprise layer; it is not required for the core self-hosted data plane.
