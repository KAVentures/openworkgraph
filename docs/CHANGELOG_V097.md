# OpenWorkGraph v0.97 — Gateway identity and employee transparency

v0.97 adds an enterprise identity layer to the optional customer-controlled Gateway while preserving the local-first evidence, v0.96 first-value activation, retention, MCP, agent-ingest and export behavior.

## Named Gateway administrators

- `/admin` supports named **owner**, **admin** and **viewer** accounts instead of requiring every administrator to act anonymously through one shared token.
- Administrators can sign in with password + authenticator code (TOTP), or with company OpenID Connect when configured.
- The first owner is bootstrapped with `OWG_GATEWAY_ADMIN_TOKEN`; subsequent administrators get single-use setup links.
- Sign-in sessions have idle and absolute expiry, repeated failures are throttled/locked, and TOTP time steps cannot be reused.
- The bootstrap token can be disabled for normal admin API use after named accounts exist. If an installation loses its only owner's credentials, deliberately re-enabling/presenting the bootstrap token can reset that owner without weakening the normal last-owner protection.
- Audit rows attribute actions to the named administrator when one is signed in.

## Employee roster and identity-bound enrollment

- Administrators can maintain an employee roster manually or by CSV, including team membership.
- Personal invitations lock enrollment to one roster employee; the computer cannot substitute another actor identity.
- A computer records how its identity was established: SSO-confirmed, personal invitation, linked by an administrator, or self-reported legacy/group enrollment.
- Organizations can require identity-bound personal invitations for all new computers.
- Offboarding revokes the employee's active device credentials and open invitations without silently deleting retained evidence.
- When SSO is required, one successful company-account confirmation authorizes **one** computer enrollment. A multi-device invitation requires a fresh confirmation for each additional computer, so a copied invite cannot reuse an earlier SSO proof.

## Employee `/me` view

- An enrolled employee can open `/me` from their own local OpenWorkGraph using a single-use, two-minute login code minted with the device credential.
- When company sign-in is enabled, `/me` may also use the roster-matching company account.
- The page is scoped to that employee and shows their connected computers, evidence held by the Gateway, organization sharing ceiling, readers with explicit access and recorded reads/changes involving them.
- Viewing evidence through `/me` is itself audited.
- If the same email belongs to more than one organization on a multi-tenant Gateway, generic SSO sign-in refuses to guess; the employee must open `/me` from an enrolled computer (or otherwise provide an organization-specific context).

## Company sign-in

- OpenID Connect authorization-code flow with PKCE (S256), nonce, single-use state, issuer/audience/expiry validation and provider-published signing keys.
- If the provider explicitly sends `email_verified: false`, sign-in is refused. Some enterprise providers omit that optional claim; in that case OWG still requires a cryptographically verified ID token, a usable email/UPN claim, optional allowed-domain match, and a matching known administrator/roster employee for the requested action.
- Optional `OWG_GATEWAY_SSO_ALLOWED_DOMAINS` can restrict accepted company-account domains.
- `OWG_GATEWAY_PUBLIC_URL` supplies the HTTPS redirect origin; Docker/self-host configuration passes the identity settings explicitly.

## Security and compatibility

- Setup/session/invitation secrets used by browser pages travel in URL fragments and are removed from the address bar on arrival; Gateway pages use no external scripts and use CSP/nonces, `no-store` and frame denial.
- Existing admin API endpoints remain available to the bootstrap token until the operator disables that path.
- Existing group invitations, managed enrollment, legacy enrollment and already-enrolled devices continue to work. Their identity is shown as self-reported until linked where appropriate.
- Existing v0.96 first-value activation, History/Retention, MCP contracts, agent telemetry, organization sharing ceilings and canonical evidence formats are preserved.
- No SCIM provisioning is included in this release.
