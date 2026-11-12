# Experimental case-scoped agent dispatch grants (#247)

This local-only opt-in path supplements the existing local MCP dispatch bearer.
The operator must first enable dispatch, save a review intention, and explicitly
offer the case. With an authenticated dashboard session, POST
`/v1/open-work/dispatch/grants` with JSON
`{"work_item_ref":"owg:w:<32 hex>","agent":"approved-agent","hours":1,"confirm_grant":true}`.
The response returns a one-time credential. Store it securely; OWG stores only
its digest. GET `/v1/open-work/dispatch/grants` lists metadata without secrets;
POST `/v1/open-work/dispatch/grants/revoke` with `{"grant_id":"<id>"}`
immediately invalidates it, including in another process using the same store.

An agent presents `Authorization: Bearer <grant-token>` to the
`/v1/open-work/dispatch/granted` GET route and the
`/granted/claim`, `/granted/release`, `/granted/report` POST routes.
Every request revalidates the current grant; the claim's agent identity comes
from the grant, never the request body. Grants are bound to exactly one opaque
case reference, expire within seven days, and cannot access general OWG APIs.
Turning dispatch off or withdrawing an offer makes the case inaccessible
through the underlying dispatch checks. Reports remain unverified claims;
these tokens **never authorize provider actions or business success**.

Limitations: the legacy local installation bearer/MCP dispatch path remains for
backward compatibility and is **not** an independently identified external
agent. The grant is scoped to the local installation, not hosted multi-tenant
OAuth; no consent-revision or employee-actor identity is bound yet. There is
no verified-receipt retrieval under this credential, no operator dashboard
grant UI, and no real-provider acceptance. This is a production-path security
integration slice, not completion of #247. Do not expose the local API publicly.

A valid grant for the same case does not authorize releasing or reporting another
agent's active lease, even if that lease token is exposed. Granted REST routes
bind lease operations to the authenticated grant's agent identity under the
same dispatch state lock. Legacy installation-local MCP calls retain their
existing lease-token interface. Reports remain unverified claims.


### Source-bound reviewed case context (experimental local API)

A newly issued dashboard grant records the currently connected source scope when
one exists. `GET /v1/open-work/dispatch/granted/case-context` requires the
same active offer generation, unexpired nonrevoked grant, connected source scope,
a separately confirmed `confirm_case_context: true` at grant issuance (default false),
and a currently retained and reviewed declaration for that exact case. Legacy
scope-less grants cannot read this context. The response excludes owner edit
CAS tokens and source scope identifiers. It is a **local-only, read-only**
owner declaration, not live provider state, a verified receipt, agent action
authority, tenant federation, or independent business outcome. No provider IO
occurs on this route. It must not be published as a hosted multi-tenant API.
A source disconnect or scope change denies access; operator grant revocation
and offer withdrawal deny access at the next request. The separate dashboard
session remains required to issue and revoke grants.

### Operator grant inventory semantics

The authenticated dashboard-session `GET /v1/open-work/dispatch/grants`
returns no bearer secrets. Its `active` flag now reflects **effective local
case access**, not just an unexpired database row: a withdrawn offer, disabled
dispatch, stale offer generation, or an agent report awaiting review marks the
grant inactive. A re-offer never revives an old grant. The separate
`case_context_approved` flag records whether disclosure was explicitly
confirmed when the credential was issued; it does **not** assert current source
consent or live case-context accessibility. The case-context endpoint always
rechecks the current source scope and review. This is owner metadata, not
hosted tenant authorization or an outcome receipt.
