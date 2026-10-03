# Microsoft 365 Copilot setup files

These are configuration templates, not a provisioned or tenant-approved app package.
Use [Microsoft 365 Agents Toolkit](https://aka.ms/M365AgentsToolkit) to create a
**Declarative Agent → Add an action → Start with an MCP server** project, using the
Gateway's HTTPS `/mcp` URL and **OAuth (with static registration)**. Toolkit creates
the tenant app manifest, identifiers, icons, authentication configuration and
provisioning workflow. Its generated `ai-plugin.json` can use the accompanying
v2.4 template after replacing both placeholders; copy `declarativeAgent.json`
into its app package. Retain Toolkit's generated outer app manifest.

The OAuth reference is a Microsoft auth configuration ID, never a bearer token,
client secret or Gateway integration token. The identity provider must issue a
**delegated access token** for the Gateway audience with the configured MCP scope,
organization and actor claims. The OAuth callback for static registration is
`https://teams.microsoft.com/api/platform/v1.0/oAuthRedirect`. Request
`offline_access` as well when using Microsoft identity and needing refresh.

Provision and sideload with Toolkit, then select this agent inside Microsoft 365
Copilot Chat and sign in. This is separate from GitHub Copilot in VS Code. Dynamic
tool discovery follows the server's tools; use Toolkit's pinned-tools option if
your organization requires a fixed approved tool inventory.

Run the verification conversation and two-user isolation checks in
[Conversational clients](../../docs/CONVERSATIONAL_CLIENTS.md) before rollout.
Tenant provisioning, consent, app policy and actual Copilot invocation require
validation in your tenant. This repository does not create those registrations.

References (reviewed 2026-10-03):
- [Microsoft's MCP plugin walkthrough](https://learn.microsoft.com/microsoft-365-copilot/extensibility/build-mcp-plugins)
- [Plugin manifest v2.4](https://learn.microsoft.com/microsoft-365-copilot/extensibility/plugin-manifest-2.4)
- [Declarative agent manifest v1.6](https://learn.microsoft.com/microsoft-365-copilot/extensibility/declarative-agent-manifest-1.6)
- [Dynamic tool discovery](https://learn.microsoft.com/microsoft-365-copilot/extensibility/plugin-dynamic-tool-discovery)
