# OpenWorkGraph (unreleased, planned v0.96)

## Gateway administrators, employee roster and the employee's own view

**Where things are**
- `https://<your-gateway>/admin`: Organization Admin Console, now with named administrator accounts.
- `https://<your-gateway>/me`: new. Each employee sees what the Gateway holds about them.
- `https://<your-gateway>/`: a start page linking to both.
- `https://<your-gateway>/join/verify`: confirms a personal invitation with company sign-in.

**Administrators**
- The first visit to `/admin` creates the first owner with the bootstrap token. After that, everyone signs in as themselves:
  - email, password (at least 12 characters) and an authenticator-app code; or
  - company sign-in, when configured.
- New administrators get a one-time setup link (72 hours) and choose their own password and authenticator.
- **Roles:** owner (everything), admin (everything except administrators), viewer (read-only, enforced by the Gateway).
- **Sign-in protection:**
  - five failures lock an account for 15 minutes;
  - repeated failures from one address are throttled;
  - each authenticator code works only once.
- **Sessions:** kept per browser tab; they end after 60 minutes idle or 12 hours in total.
- **Audit:** the audit log names the administrator (`admin:it@acme.se`). The bootstrap token appears as `bootstrap-token`.
- `OWG_GATEWAY_ADMIN_TOKEN_API=disabled` stops the bootstrap token from administering the Gateway once named administrators exist.

**Employees and computers**
- **Roster:** add employees one at a time or by CSV (email, name, teams). The employee's Gateway identity is their normalized work email; teams feed the human access API.
- **Personal invitations:** lock the computer's identity to one employee. The local join screen shows "You will join as …" with no identity field, and the Gateway ignores any identity the computer sends.
- **Company sign-in:** when SSO is configured, personal invitations by default require the employee to confirm with their company account before joining.
- **Identity source:** every computer shows how its identity was established:
  - SSO verified;
  - personal invite;
  - linked by admin;
  - self-reported (group invitations, managed files, legacy codes).
- **Linking:** self-reported computers can be linked to an employee, optionally re-attributing the evidence they already sent.
- **Require a personal invitation for every new computer:** turns off group invitations, managed files, single-use enrollment codes and the legacy enrollment token for new computers.
- **Offboarding:** revokes an employee's computers and open invitations at once.

**Employee view (`/me`)**
- From their own OpenWorkGraph (**Organization → See what your organization holds about you**, a one-time link valid for two minutes), or with company sign-in.
- **What it shows:**
  - the employee's computers and the evidence the Gateway holds from them;
  - what may be shared;
  - who can read it;
  - every recorded read or change about them, including organization-wide integration reads.
- Viewing evidence there is itself recorded. Sessions end after 30 minutes idle or 8 hours.

**Company sign-in (OpenID Connect)**
- Authorization-code flow with PKCE, a nonce and a single-use 10-minute state.
- The ID token is verified against the provider's published keys, issuer, audience and expiry.
- Unverified emails are refused; allowed email domains can be set.
- **Configuration:** `OWG_GATEWAY_PUBLIC_URL`, `OWG_GATEWAY_SSO_ISSUER`, `OWG_GATEWAY_SSO_CLIENT_ID`, `OWG_GATEWAY_SSO_CLIENT_SECRET`, `OWG_GATEWAY_SSO_ALLOWED_DOMAINS`. The redirect URI is `<public URL>/sso/callback`. All are passed through `deploy/docker-compose.yml`.
- The first company sign-in links an employee's provider subject to the roster. Human access API tokens that identify people by an opaque `sub` then read that employee's own evidence.

**Security details**
- Secrets in links (setup, session, `/me` codes, invitation codes) travel in the URL fragment. They never reach server logs and are removed from the address bar on arrival.
- The pages load no external scripts and run under a per-response CSP nonce.

**Compatibility**
- Existing admin APIs keep working with the bootstrap token until it is disabled.
- Existing group invitations, managed files and enrolled computers keep working. Their computers show as self-reported until linked.
- No change to the evidence data plane or the local product.

Setup: [ORGANIZATION_ROLLOUT.md](ORGANIZATION_ROLLOUT.md).
