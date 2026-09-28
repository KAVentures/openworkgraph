# Rolling OpenWorkGraph out to a team

OpenWorkGraph stays local-first. An organization can optionally run its own Gateway and let enrolled employee computers synchronize only the evidence allowed by both the employee endpoint and the organization policy.

## Where everything is

| Page | Who | Address | Purpose |
|---|---|---|---|
| **Gateway start page** | anyone | `https://<your-gateway>/` | Links to the two pages below; shows nothing else |
| **Organization Admin Console** | IT / Gateway administrators | `https://<your-gateway>/admin` | Employees, invitations, computers, sharing ceilings, retention, service tokens, administrators, identity settings and audit |
| **What your organization holds about you** | each employee | `https://<your-gateway>/me` | The employee's own computers, the evidence the Gateway holds from them, who can read it, and every recorded read |
| **Invitation confirmation** | an invited employee | `https://<your-gateway>/join/verify` | Confirms a personal invitation with company sign-in, when the invitation requires it |
| **Personal dashboard · this computer** | each employee | local OpenWorkGraph dashboard | This computer's local evidence, AI connections, capture and organization-sharing controls |

The admin console has a distinct indigo **ADMIN** header and an explicit note that it is not the employee dashboard. The employee page has a green **YOU** header. A managed employee installation visibly shows **Managed by <organization>** and what the organization is permitted to receive.

Employees normally reach `/me` from their own OpenWorkGraph: **Organization → What your organization holds about you → See what your organization holds about you**. That button mints a personal link that works once, for two minutes, and signs the employee in on the Gateway as the person their computer is enrolled as. With company sign-in configured, employees can also open `/me` directly and sign in.

## 1. Prepare the Gateway

1. Deploy the Gateway as described in [SELF_HOSTING.md](SELF_HOSTING.md), behind HTTPS and with PostgreSQL for a real pilot. Set `OWG_GATEWAY_PUBLIC_URL` to its public address.
2. Open `https://<your-gateway>/admin`. With no administrator yet, the console asks for the bootstrap token (`OWG_GATEWAY_ADMIN_TOKEN`) and your work email, and creates you as the first **owner**.
3. Set up your account on the next screen: add the Gateway to an authenticator app (Google Authenticator, Microsoft Authenticator, 1Password or similar), choose a password of at least 12 characters, and confirm with a code. You are signed in.
4. Add other administrators under **Administrators** (next section).
5. Open **Sharing policy** and set the organization's sharing ceiling.
6. Once your administrators are set up, set `OWG_GATEWAY_ADMIN_TOKEN_API=disabled` and restart, so the shared token can no longer administer the Gateway.

## 2. Administrators

Every administrator has a named account. Admin actions in the audit log show who did them (`admin:it@acme.se`), not a shared token.

| Role | Can |
|---|---|
| **Owner** | everything, including managing administrators |
| **Admin** | everything except managing administrators |
| **Viewer** | read-only: the console hides write controls and the Gateway refuses writes |

- **Adding one:** an owner adds the administrator's work email and role, and sends them the one-time setup link. The link works once and expires after 72 hours; the new administrator chooses their own password and authenticator.
- **Sign-in:** email, password and a code from the authenticator app. With company sign-in configured, administrators can use **Sign in with company account** instead; the email from the identity provider must match their administrator account.
- **Sessions:** kept only in that browser tab. They end after 60 minutes idle, after 12 hours in total, and when the administrator signs out.
- **Protection:** five failed sign-ins lock the account for 15 minutes; repeated failures from one address are throttled. Each authenticator code works only once.
- **Reset sign-in:** gives the administrator a new setup link and ends their sessions.
- **Disable:** ends their sessions immediately. The last active owner cannot be demoted or disabled.

**Recovery:**
- **Another owner is available:** they use **Reset sign-in** on the owner who lost their authenticator.
- **No owner is available:** set `OWG_GATEWAY_ADMIN_TOKEN_API=enabled`, restart, and open `/admin`. Choose **Use the bootstrap admin token instead**, reset the owner's sign-in, then disable the token again. Actions taken with the token are audited as `bootstrap-token`.

## 3. Decide what the organization may receive

Organization policy can restrict an endpoint; it cannot force the endpoint to share more than its own local policy permits.

Examples:

- window/page titles can be removed;
- structural metadata can be removed;
- event types can be allow-listed;
- excluded-app evidence can remain local;
- the organization can require **Redacted** AI context on enrolled computers;
- structural AI-agent activity can be **permitted** or forbidden by the organization.

Permitting agent activity does not enable it for an employee. Agent evidence is synchronized only when **both** the organization policy and the endpoint-local policy allow it.

Typed text, clipboard contents, password values and screenshot bytes are outside the Gateway sharing contract.

## 4. Add employees

Under **Employees**, add people one at a time (work email, name, teams) or paste a CSV:

```text
email,name,teams
anna.svensson@acme.se,Anna Svensson,sales;nordics
erik.lindqvist@acme.se,Erik Lindqvist,support
```

The header row is optional. Rows with errors are reported by line number and the rest are imported. Importing an existing email updates the name and teams.

An employee's identity on the Gateway is their normalized work email. Teams decide which team leads can read their evidence through the human access API (see [HUMAN_ACCESS.md](HUMAN_ACCESS.md)).

## 5. Invite employees' computers

### Recommended — personal invitations

Click **Personal invite** next to an employee. The console shows an `owgjoin1.…` code and a ready-to-send message.

- **Locked identity:** the invitation can only connect computers as that employee. OpenWorkGraph shows "You will join as Anna Svensson (anna.svensson@acme.se)"; there is no identity field to type into, and the Gateway ignores any identity the computer sends.
- **Limits:** valid for 7 days and up to 2 computers. Through the admin API (`expires_days`, `max_devices`), up to 30 days and 5 computers.
- **Consuming a seat:** previewing never consumes one, and neither does a rejected enrollment. A seat is used in the same database transaction that creates the computer's credential.
- **Company sign-in:** when SSO is configured, a personal invitation requires the employee to confirm with their company account first. OpenWorkGraph shows **Confirm with company account**, which opens `/join/verify` on the Gateway. The **Join** button stays disabled until the sign-in matches the invited email. Signing in as someone else is refused.

Employees join from OpenWorkGraph → **Organization** → **Join your organization**:

1. Paste the code.
2. Review the organization name and exactly what the Gateway policy permits.
3. Confirm with the company account, if asked.
4. Confirm that you reviewed the sharing preview.
5. Join.

### Group invitations and managed devices

**Group invitations** (one reusable code for many computers) and the managed `managed.json` file still work. With these, the employee types their identity, or it is derived on the computer. The Gateway cannot verify it.

Such computers appear under **Employees → Computers not linked to an employee** as **self-reported**. For each one, either:

- **link it** to the right employee, optionally re-attributing the evidence it already sent; or
- **revoke** it.

The managed file is deployed with OpenWorkGraph through Jamf, Intune or another device-management system to:

- macOS: `/Library/Application Support/OpenWorkGraph/managed.json`
- Windows: `C:\ProgramData\OpenWorkGraph\managed.json`
- Linux: `/etc/openworkgraph/managed.json`

`OWG_MANAGED_CONFIG` can override the path for testing or custom packaging.

On first launch, OpenWorkGraph previews the Gateway policy and joins automatically. The employee dashboard visibly reports **Managed by <organization>** and displays the sharing policy. Managed enrollment never hides organization sharing from the employee.

Identity defaults to the OS username, optionally combined with `email_domain`. For environments where OS usernames do not map cleanly to work identities, deploy an explicit `actor_id` per computer instead.

Use short invitation expiry periods and an appropriate seat limit for managed deployment, and protect the managed file with normal device-management permissions. Revoke the invitation after the intended rollout is complete. If a computer is already enrolled with a different Gateway or organization, managed setup fails visibly instead of silently replacing the existing enrollment.

### Requiring verified identities

Under **Identity & sign-in**, **Require a personal invitation for every new computer** turns off group invitations, managed files, single-use enrollment codes and the legacy enrollment token for new computers. Computers already enrolled are not affected; link or revoke them on the Employees tab.

### How each computer's identity was established

| Label | Meaning |
|---|---|
| **SSO verified** | joined with a personal invitation confirmed by company sign-in |
| **personal invite** | joined with a personal invitation |
| **linked by admin** | joined another way; an administrator linked it to the employee |
| **self-reported** | joined with a group invitation, managed file, single-use enrollment code or legacy token; identity typed or derived on the computer |

## 6. Company sign-in (SSO)

Company sign-in is optional. Without it, administrators use a password and authenticator app, and employees reach `/me` from their own OpenWorkGraph.

1. In your identity provider (Entra ID, Okta, Google Workspace, Keycloak or any OpenID Connect provider), register a web application:
   - redirect URI: `https://<your-gateway>/sso/callback`;
   - scopes: `openid email profile`.
2. Set, then restart the Gateway:

   ```text
   OWG_GATEWAY_PUBLIC_URL=https://<your-gateway>
   OWG_GATEWAY_SSO_ISSUER=https://login.example.com/...   # defaults to OWG_GATEWAY_OIDC_ISSUER
   OWG_GATEWAY_SSO_CLIENT_ID=...
   OWG_GATEWAY_SSO_CLIENT_SECRET=...                      # omit for a public client
   OWG_GATEWAY_SSO_ALLOWED_DOMAINS=acme.se,acme.com        # optional
   ```

3. **Identity & sign-in** now shows the issuer and redirect URI. The sign-in screens offer **Sign in with company account**.

The Gateway uses the authorization-code flow with PKCE, a nonce and a single-use state that expires after 10 minutes. It verifies the ID token signature against the provider's published keys, the issuer, the audience and the expiry. Sign-in is refused when:

- the provider marks the email unverified;
- the email's domain is not in the allowed list;
- the email does not belong to an administrator (for `/admin`) or a roster employee (for `/me`).

The first successful company sign-in links the employee's provider subject to their roster entry. Human access API tokens that identify people by an opaque `sub` then read that employee's own evidence.

## 7. What employees can see about themselves

`/me` shows the employee, and only the employee:

- **Evidence:** how much the Gateway holds from them, and exactly which events.
- **Sharing:** what their computers are allowed to share.
- **Who can read it:**
  - the employee;
  - team leads with access to their teams;
  - the number of integrations with organization-wide read access.

  Gateway administrators manage computers and policy but cannot read evidence without an explicit access scope.
- **Computers:** each one, and how it was linked to them.
- **Recorded reads and changes:**
  - their own reads and team leads' reads;
  - integration reads of their evidence or of everyone's evidence;
  - invitations, enrollments, links, team changes and offboarding.

Viewing evidence on `/me` is itself recorded as a read by the employee.

Employee sessions end after 30 minutes idle, after 8 hours in total, or when the employee signs out. A `/me` link from OpenWorkGraph stops working if that computer is revoked.

## 8. Privacy boundary at enrollment

Enrollment starts organization sharing from the current local evidence position. Evidence recorded before the computer joined remains local by default.

Pausing organization sharing also keeps evidence local for that paused interval; resuming does not silently backfill the paused period.

Local capture continues independently if the Gateway is offline or organization sharing is paused.

## 9. Operate the pilot

The admin console provides:

- **Overview:** employees, enrolled computers, recent evidence activity, silent computers, unverified identities, open group invitations and deployment warnings.
- **Employees:** the roster, personal invitations, each employee's computers and how they were linked, computers not linked to an employee, and offboarding.
- **Group invitations:** reusable, expiring, revocable enrollment links and the managed file.
- **Sharing policy:** the organization sharing ceiling.
- **Retention:** Gateway retention configuration.
- **Access tokens:** scoped integration credentials, shown once at creation and revocable later.
- **Administrators** (owners only): named administrator accounts and roles.
- **Identity & sign-in:** require verified identities, company sign-in status, bootstrap token status.
- **Audit log:** administrative, enrollment and evidence-access activity, attributed to the named administrator.

**Offboarding** an employee revokes all their computers and open invitations at once. Revoking a group invitation prevents new enrollments but does not revoke computers that already received credentials; revoke those separately.

## Current rollout limits

- Group invitations and managed files still rely on identity typed or derived on the computer; require personal invitations for verified identities.
- SCIM provisioning is not included; add employees by hand or CSV, or through the admin API (`POST /v1/admin/employees/{organization}` and `/import`).
- The managed paths should be dogfooded on real macOS and Windows machines before a broad fleet rollout.
- The browser sensor remains a separate installation/pairing surface unless IT packages/distributes the browser extension through its own browser-management policy.
- Organization rollout does not enable collection of general typed text or clipboard contents.
