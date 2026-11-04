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
