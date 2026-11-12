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
no case-context/receipt retrieval under this credential, no operator dashboard
grant UI, and no real-provider acceptance. This is a production-path security
integration slice, not completion of #247. Do not expose the local API publicly.
