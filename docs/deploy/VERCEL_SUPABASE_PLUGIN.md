# Vercel + Supabase production wiring

This runbook is intentionally split into two deployments of the same repository and one database.

## Security boundary

- `mcp.owg.kinvectum.com`: read-only ChatGPT/Codex MCP resource server. Set `OWG_VERCEL_SERVICE=plugin`.
- `gateway.owg.kinvectum.com`: desktop enrollment and privacy-hardened evidence sync. Set `OWG_VERCEL_SERVICE=gateway`.
- Both use the dedicated OWG Supabase Postgres project. Do not expose Gateway tables through the Supabase Data API.
- Keep Vercel preview deployments free of production database/OAuth secrets.

## Supabase OAuth 2.1

In the OWG Supabase project:

1. Authentication -> OAuth Server: enable OAuth 2.1.
2. Use an asymmetric JWT signing key (RS256 or ES256); do not use HS256 for OIDC.
3. Enable Dynamic Client Registration so ChatGPT can register when configured for DCR.
4. Configure a real authorization/consent UI under the OWG public site. Do not auto-consent.
5. The deployment uses:
   - issuer: `https://tohekeiamafjjfduzqzq.supabase.co/auth/v1`
   - JWKS: `https://tohekeiamafjjfduzqzq.supabase.co/auth/v1/.well-known/jwks.json`
   - required scope: `openid`
   - JWT audience: `authenticated`
6. Confirm OAuth discovery advertises authorization-code flow and PKCE S256 before connecting ChatGPT.

The MCP resource identifier remains `https://mcp.owg.kinvectum.com/mcp`. Token audience and MCP resource identifier are deliberately separate configuration values.

## Database

Use Supabase's transaction pooler (port 6543) over TLS. The runtime database credential is a Vercel secret and must never be committed.

The Gateway tables belong in the private `owg_gateway` schema. Ensure the runtime connection's role/search path resolves `owg_gateway` first. Do not grant `anon`, `authenticated`, or `service_role` access to that schema.

## Vercel plugin production variables

Production only:

- `OWG_VERCEL_SERVICE=plugin`
- `OWG_GATEWAY_DATABASE_URL=<secret transaction-pooler URL>`
- `OWG_PLUGIN_OAUTH_ISSUER=https://tohekeiamafjjfduzqzq.supabase.co/auth/v1`
- `OWG_PLUGIN_OAUTH_JWKS_URL=https://tohekeiamafjjfduzqzq.supabase.co/auth/v1/.well-known/jwks.json`
- `OWG_PLUGIN_OAUTH_ALGORITHMS=RS256,ES256`
- `OWG_PLUGIN_REQUIRED_SCOPE=openid`
- `OWG_PLUGIN_TOKEN_AUDIENCE=authenticated`
- `OWG_PLUGIN_RESOURCE_URL=https://mcp.owg.kinvectum.com/mcp`
- `OWG_PLUGIN_GATEWAY_URL=https://gateway.owg.kinvectum.com`
- `OWG_PLUGIN_PROFILE_KEY=<random secret>`
- `OWG_PLUGIN_RATE_LIMIT_PER_MINUTE=120`

Keep the OpenAI domain-challenge variable unset until the submission portal supplies the challenge token.

## Vercel Gateway production variables

Production only:

- `OWG_VERCEL_SERVICE=gateway`
- `OWG_GATEWAY_DATABASE_URL=<same secret transaction-pooler URL>`
- strong Gateway admin/enrollment credentials only where the Gateway requires them.

The Gateway hostname is not the ChatGPT MCP URL.

## Verification before dogfood

1. Custom plugin domain is publicly reachable without Vercel login.
2. `/.well-known/oauth-protected-resource` advertises the Supabase authorization server and `openid`.
3. Unauthenticated MCP tool access is rejected with an OAuth challenge.
4. Supabase discovery advertises PKCE S256 and a supported client-registration path.
5. A real OAuth token is checked for issuer, signature, expiry, subject, `aud=authenticated`, and `openid`.
6. The plugin lists exactly the intended read-only tools.
7. Create a personal device-link grant; use it once from local OWG; verify reuse fails.
8. Verify only post-enrollment privacy-hardened evidence syncs by default.
9. Ask ChatGPT continuity, search, chronology, repeated-work, and previous-agent prompts against real synced evidence.
10. Review Vercel and Gateway audit logs for identity leakage or unexpected writes.

Do not submit publicly until the real OAuth/device-link/sync/read path passes end-to-end.
