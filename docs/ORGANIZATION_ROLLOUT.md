# Rolling OpenWorkGraph out to a team

OpenWorkGraph stays local-first. An organization can optionally run its own Gateway and let enrolled employee computers synchronize only the evidence allowed by both the employee endpoint and the organization policy.

## Two deliberately different screens

| Screen | Who | Where | Purpose |
|---|---|---|---|
| **Organization Admin Console** | IT / Gateway administrator | `https://<your-gateway>/admin` | Enrollment, fleet status, sharing ceilings, retention, service tokens and audit |
| **Personal dashboard · this computer** | Employee | local OpenWorkGraph dashboard | This computer's local evidence, AI connections, capture and organization-sharing controls |

The admin console has a distinct indigo **ADMIN** header and an explicit warning that it is not the employee dashboard. A managed employee installation visibly shows **Managed by <organization>** and what the organization is permitted to receive.

## 1. Prepare the Gateway

1. Deploy the Gateway as described in [SELF_HOSTING.md](SELF_HOSTING.md), preferably behind HTTPS with PostgreSQL for a real pilot.
2. Open `https://<your-gateway>/admin`.
3. Sign in with the configured `OWG_GATEWAY_ADMIN_TOKEN`, then enter the organization ID and display name.
4. Open **Sharing policy** and set the organization's sharing ceiling.

The admin bootstrap token is currently a shared administrator credential. For a larger rollout, put the Gateway behind the organization's normal access controls and plan named administrator SSO rather than distributing this token broadly.

## 2. Decide what the organization may receive

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

## 3. Invite employees

### Option A — reusable join code

In **Invite employees**, choose a label, maximum number of computers and expiry. One invitation can enroll multiple computers, while each successful enrollment receives its own device credential.

Send the generated `owgjoin1.…` code to the intended employees. Treat it like an enrollment secret: it cannot read organization evidence, but until it expires, is exhausted or is revoked, it can enroll another computer.

The employee opens OpenWorkGraph → **Organization** → **Join your organization** and:

1. pastes the code;
2. reviews the organization name and exactly what the Gateway policy permits;
3. enters their work identity;
4. confirms that they reviewed the sharing preview;
5. joins.

Previewing never consumes a seat. A rejected enrollment does not consume a seat either. A seat is committed in the same database transaction that creates the device credential.

### Option B — managed devices / MDM

The admin console can download a small `managed.json` containing the join code. Deploy it with OpenWorkGraph through Jamf, Intune or another device-management system to:

- macOS: `/Library/Application Support/OpenWorkGraph/managed.json`
- Windows: `C:\ProgramData\OpenWorkGraph\managed.json`
- Linux: `/etc/openworkgraph/managed.json`

`OWG_MANAGED_CONFIG` can override the path for testing or custom packaging.

On first launch, OpenWorkGraph previews the Gateway policy and joins automatically. The employee dashboard visibly reports **Managed by <organization>** and displays the sharing policy. Managed enrollment never hides organization sharing from the employee.

Identity defaults to the OS username, optionally combined with `email_domain`. For environments where OS usernames do not map cleanly to work identities, deploy an explicit `actor_id` per computer instead.

Use short invitation expiry periods and an appropriate seat limit for managed deployment, protect the managed file with normal device-management permissions, and revoke the invitation after the intended rollout is complete.

If a computer is already enrolled with a different Gateway or organization, managed setup fails visibly instead of silently replacing the existing enrollment.

## 4. Privacy boundary at enrollment

Enrollment starts organization sharing from the current local evidence position. Evidence recorded before the computer joined remains local by default.

Pausing organization sharing also keeps evidence local for that paused interval; resuming does not silently backfill the paused period.

Local capture continues independently if the Gateway is offline or organization sharing is paused.

## 5. Operate the pilot

The admin console provides:

- **Overview** — enrolled computers, recent evidence activity, silent devices, active invitations and deployment warnings;
- **Invite employees** — reusable, expiring, revocable enrollment links;
- **Employees & devices** — actor/device identity, enrollment time, last evidence time and explicit revocation;
- **Sharing policy** — the organization sharing ceiling;
- **Retention** — Gateway retention configuration;
- **Access tokens** — scoped integration credentials, shown once at creation and revocable later;
- **Audit log** — Gateway administrative and evidence-access activity.

Revoking an invitation prevents new enrollments but does not revoke computers that already received device credentials. Revoke a computer separately from **Employees & devices** when required.

## Current rollout limits

- Gateway administrator sign-in still uses the bootstrap admin token; named administrator SSO is a later hardening step.
- The managed paths should be dogfooded on real macOS and Windows machines before a broad fleet rollout.
- The browser sensor remains a separate installation/pairing surface unless IT packages/distributes the browser extension through its own browser-management policy.
- Organization rollout does not enable collection of general typed text or clipboard contents.
