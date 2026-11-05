# Connecting business systems with OAuth

Salesforce, ServiceNow, Microsoft 365, Atlassian (Jira) and Zendesk can be
connected with a sign-in instead of a pasted token.

1. Register an app in your own tenant (a Salesforce connected app, a ServiceNow
   OAuth application registry entry, a Microsoft Entra app registration, an
   Atlassian 3LO app or a Zendesk OAuth client). Add the redirect URI
   `http://127.0.0.1:8787/v1/connector-oauth/callback` and grant only the
   read scopes the connector needs.
2. In **Privacy → Business system sign-in**, choose the provider, enter your host
   (or Microsoft tenant), the client ID and, only where the provider requires
   it, the client secret. Click **Connect** and approve access in the new tab.
3. Connector commands use the sign-in when
   `OWG_<NAME>_OAUTH_CONNECTION=<connection_id>` is set, for example
   `OWG_SERVICENOW_OAUTH_CONNECTION` or `OWG_SALESFORCE_READ_OAUTH_CONNECTION`.
   Otherwise they keep using the existing token variables
   (`OWG_SERVICENOW_TOKEN`, ...). A sign-in for a different provider is refused.

How it works:

- Authorization-code flow with PKCE (S256) and a single-use `state` that expires
  after 10 minutes. The local callback is the only unauthenticated route, and it
  can only complete a sign-in that this dashboard started.
- Refresh tokens are stored AES-256-GCM-encrypted in the auth folder
  (`.connector_oauth.bin`), keyed by this installation's local secret. Access
  tokens are kept in memory only. No API, export or AI tool returns a token.
- Rotating refresh tokens (Atlassian, Microsoft, Salesforce when configured) are
  saved before the new access token is used, under a cross-process lock.
- **Disconnect** calls the provider's revocation endpoint where one exists
  (Salesforce, ServiceNow, Zendesk), then forgets the sign-in. Microsoft and
  Atlassian have no token-revocation endpoint for this flow; remove the app's
  consent in their admin settings.
- Connecting reads nothing by itself: each connector still requires its own
  explicit consent, selection and bounds before any read.

What this does not establish: customer admin approval, tenant-wide consent
policy, or live acceptance against your provider. Run each connector's own
acceptance steps in a sandbox tenant first.

## Retained Jira ingestion with rotating tokens (#436)

Retained Jira profiles verify the signed-in account and require both
`read:jira-work` and `read:jira-user`, plus `offline_access` for refresh. New
Atlassian sign-ins request these scopes by default. Existing sign-ins keep the
scopes previously granted: if they lack `read:jira-user`, create a new sign-in,
approve the scopes at Atlassian, select its connection ID, and use the retained
profile's **Verify renewed credential** action. OWG preserves the existing
profile only after the current site and account match its prior binding. This
change does not add scopes to an existing grant or authorize reads by itself.

Set `OWG_JIRA_OAUTH_CONNECTION=<connection_id>` (an Atlassian sign-in) instead of
`OWG_JIRA_OAUTH_ACCESS_TOKEN`. Retained profiles and source receipts then get a
fresh access token from the sign-in, renewing it with the rotating refresh token
when it expires.

- A profile binds to the sign-in (`credential_reference: oauth_connection:<id>`),
  not to the short-lived token, so routine renewal keeps its dataset, namespace
  and checkpoint. Every read still checks the site grant and the same account,
  exactly as before.
- Switching a profile from a pasted token to a sign-in, or to another sign-in,
  is a credential change and goes through the existing same-account rebind.
- A refresh token that has expired, was already used or was revoked fails closed
  with `jira_oauth_renewal_failed_reconnect_required`. Nothing retries with
  another token or adopts a different account.
- The replacement refresh token is saved, under the cross-process lock, before
  the new access token is used. If the provider issues a new token but the
  response is lost, the old refresh token may be rejected next time; reconnect
  then.
