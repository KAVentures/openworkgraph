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

## Distribution channels (independent, additive)

**The desktop OpenWorkGraph product is not a ChatGPT plugin dependency.**
These are independent supported entry points:

- **Standalone macOS/Windows:** the packaged, eventually signed/notarized native
  application starts and observes locally, with its own UI, local SQLite, exports,
  local MCP/agent integrations and optional browser extension. It needs neither an
  OWG cloud account nor ChatGPT. Existing install/start-at-login/update behavior,
  AI access defaults, and consent settings must not change for plugin support.
- **Bring-your-own AI/agent:** existing local MCP stdio, direct supported client
  connections, REST and file export stay available. A future simplified handoff
  should reuse these established interfaces rather than routing all users through
  the public plugin or requiring cloud data upload.
- **ChatGPT hosted app:** an *optional* read-only remote consumer of selectively
  synchronized Gateway evidence. A user can authenticate in ChatGPT first,
  before installing a recorder, and receive an explicit no-evidence onboarding
  hint. Signing in never starts recording, uploads local history, or enrolls a
  device. The desktop Connect ChatGPT action is an optional pairing method, not a
  prerequisite to a standalone installation.

For the hosted app, only the HTTPS MCP endpoint is needed by ChatGPT; the desktop
installer is a separate user-approved OS operation. The remote MCP service cannot
silently install macOS/Windows software or grant Accessibility/Input Monitoring.
If the user chooses to link a recorder, explicitly confirm forward-only evidence
sharing and use the existing enrollment boundary. Previously recorded evidence
remains local unless the user takes another explicitly authorized action.

**Regression scope for every hosted-integration change:** verify installers,
autostart, local-only use with no account/network, local stdio MCP, exports,
AI-access/Observe separation, demo mode, existing enterprise Gateway enrollment,
and the optional personal OAuth flow. Public MCP changes should be isolated to
`gateway/public_plugin_mcp.py` and should not alter local collectors or local
MCP auth/transport defaults.

## Public OAuth metadata compatibility

Expose the same canonical protected-resource metadata at both
`/.well-known/oauth-protected-resource/mcp` (RFC 9728 for the `/mcp` resource)
and `/.well-known/oauth-protected-resource` (host-root fallback for clients).
Both report `https://mcp.owg.kinvectum.com/mcp` as the **same** resource ID and
the same Supabase issuer. The additional endpoint is read-only discovery, not
a second OAuth service, token issuer, data store, or access bypass.

## Personal desktop linking

In the local desktop dashboard, choose **Connect ChatGPT → Sign in to OWG**.
Use the same account that will authorize ChatGPT. The desktop registers a public
OAuth client and uses authorization-code flow with PKCE S256 and a single-use
state. The browser returns to the desktop's loopback callback. OAuth tokens and
the short-lived device-link grant stay in desktop process memory; the dashboard
never receives them.

Sign-in does not enroll or upload evidence. Choose **Link and share new evidence**
to enroll through the existing Gateway connector. The connector begins at the
current local evidence boundary; earlier history stays local. Observe, AI access,
and agent-sharing preferences are unchanged. Existing Gateway connections must
be disconnected explicitly first. Demo mode cannot link. Cancel or restart the
flow after a denial, timeout, process restart, or failed enrollment.

Add `https://mcp.owg.kinvectum.com/mcp` as an OAuth app in ChatGPT and sign in with
the same account. This requires both public service domains to resolve and both
services to use the configured private Postgres database.

## DNS and token trust

Vercel domain ownership verification does not create DNS records at an external
DNS provider. If `kinvectum.com` uses Hostinger nameservers, add CNAME records
there for `mcp.owg` and `gateway.owg`, using each project's target from Vercel
**Settings → Domains**. Preserve the existing `owg` record and nameservers.
Check public DNS and HTTPS before attempting the live verification steps above.

The dedicated Supabase issuer is the plugin's trust boundary. Tokens from other
OAuth clients on that same project can be accepted when they have the configured
audience and scope. Do not reuse this project for unrelated applications. The
plugin accepts only the configured asymmetric algorithms, regardless of wider
algorithms advertised in discovery. Desktop linking always requests PKCE S256.
