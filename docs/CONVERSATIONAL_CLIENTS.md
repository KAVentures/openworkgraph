# Work in your usual AI conversation

OpenWorkGraph records permitted work in the background. Your ordinary AI client
retrieves evidence when you discuss work, asks about what observation cannot
establish, and drafts the automation with you. You do not need to run a second
interview or workflow-authoring wizard in OpenWorkGraph.

## Local clients

Connect the compact stdio MCP launcher through Connections. Local AI access and
individual client switches apply to every evidence and knowledge tool. In Privacy,
choose whether to include older history for 24 hours or **until I revoke access**.
History also offers selected dates with either temporary or standing access.
Standing access is an explicit choice; the default remains off. Turning the master
AI switch off blocks every local MCP read, but the separate saved-history grant
remains until its own expiry or explicit revocation; turning AI access back on can
therefore resume that still-valid grant. The optional reset-on-restart setting
still applies. Retention and AI disclosure remain separate choices.

Use `search_work` with concrete app names, document terms and dates. Search is
lexical, not semantic: try synonyms and inspect chronological pages before saying
something did not occur. `find_repeated_workflows` now respects the current run or
selected grant rather than requiring all saved history. Pass the returned execution
IDs **and the relevant dates** to `get_workflow_evidence`. Source limits are disclosed;
a reached limit may omit older or partial runs. Narrow the dates instead of treating
a bounded sample as a complete inventory.

## Remember explanations between conversations

`get_workflow_knowledge` retrieves portable reviewed procedures, explicitly stated
explanations, decision rules, unanswered questions and evidence references.
`save_workflow_knowledge` requires `user_confirmed=true` after the AI shows the
complete record and the person explicitly agrees. This is **client-declared review**;
OpenWorkGraph cannot verify who clicked or spoke in an external client. A client
must honor its tool confirmation controls and this review contract. Never silently
save an AI draft or promote captured chat into business rules.

A workflow has a stable user-chosen ID and numbered revisions. Updates require the
current `expected_revision`, preventing silent replacement of newer rules.
`forget_workflow_knowledge` deletes all versions. Tool responses are portable JSON;
clients can retain/export that same JSON. Reviewed knowledge is kept separately
from captured history until explicitly forgotten, and does not grant execution
permission or establish organization policy. History deletion removes evidence,
not independently reviewed explanations; evidence references can become unavailable.

Ordinary ChatGPT and Microsoft 365 conversations are not automatically imported.
The existing optional local agent-message sensors remain separate. The local store
and an organization's Gateway store are also separate: neither silently uploads
reviewed knowledge to the other. Ask the AI to export a reviewed record if you
intend to transfer it, and explicitly review the destination save.

## Customer-hosted remote MCP

Cloud clients need a reachable authenticated endpoint. The human Gateway now mounts
Streamable HTTP MCP at `/mcp` when explicitly configured, with OAuth protected
resource metadata and bearer challenges supplied by the MCP SDK. It uses the
existing JWT/JWKS verifier and each signed-in person's organization/actor claims.
Every query supplies both filters, including cursor pages. There is no shared
service token or organization-wide actor selector on this conversational surface.
Knowledge reads require self evidence access; writes additionally require
`self:knowledge:write` mapped from an administrator-configured identity group.

Example environment (replace the provider and claims for your installation):

```dotenv
OWG_GATEWAY_MCP_RESOURCE_URL=https://work.example.com/mcp
OWG_GATEWAY_MCP_OAUTH_SCOPE=api://YOUR_RESOURCE_APP_ID/WorkContext.Read
OWG_GATEWAY_OIDC_ISSUER=https://YOUR_IDENTITY_ISSUER
OWG_GATEWAY_OIDC_AUDIENCE=YOUR_RESOURCE_APP_ID
OWG_GATEWAY_OIDC_JWKS_URL=https://YOUR_PROVIDER_JWKS_URL
OWG_GATEWAY_OIDC_ORGANIZATION_CLAIM=owg_org
OWG_GATEWAY_OIDC_ACTOR_CLAIM=owg_actor
OWG_GATEWAY_OIDC_GROUP_SCOPE_MAP={"YOUR_WRITERS_GROUP_ID":["self:knowledge:write"]}
```

Run the existing `gateway.human_enterprise_app:app` deployment behind HTTPS;
Compose passes these settings through. Configure a delegated OAuth authorization
code client with your IdP for each host, consent its scope, and ensure claims map
to **the same actor and organization IDs as enrolled devices**. For Entra, use the
correct tenant issuer/JWKS, API audience and claims mapping; `oid` may be the
appropriate actor claim when enrollment uses that identity. The scope in `scp`
may be the last component of the advertised API scope. Tokens without the delegated
scope, including application-only or identity tokens, are refused by remote MCP.

The Gateway validates signature, issuer, audience, expiry and delegated scope.
It is an OAuth resource server, not an authorization server: client registration,
consent, refresh, token revocation and OAuth discovery belong to your provider.
Static registration is supported by configuration; DCR is available only if your
provider supports it. Requests authenticate again; already issued JWTs generally
remain valid until expiry unless the provider/verifier implements revocation.
Keep access-token lifetimes appropriate to your deployment.

Remote tools cover current synced context, lexical search, paginated traces,
repeated candidates, evidence bundles, automation guidance and reviewed knowledge.
They do not expose local-only file reads or agent-message history. Current context
can lag behind device capture. The local master switch controls local MCP;
**Gateway identity permissions control data already synced to the Gateway**.
Turning local sharing off stops future sharing, not existing cloud access.

### ChatGPT

Enable the account's supported custom MCP/developer-mode workflow, add your public
Gateway `/mcp` URL, and configure OAuth using your provider's client registration.
Sign in as the person whose device evidence was enrolled. Confirm that ChatGPT
lists the OWG tools and actually calls them in a test conversation. Availability
and UI differ by account/workspace; this repository's protocol tests do not attest
a live ChatGPT connection. See the [official setup guide](https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt).

For a local-only installation, a supported secure tunnel is a separate route.
The dashboard's temporary HTTP bridge binds only to loopback; starting it does not
connect ChatGPT. Use the fresh endpoint and bearer with your supported tunnel.
Stopping the bridge or restarting OpenWorkGraph invalidates that bearer: start a
new bridge and update the tunnel. Prefer stdio for local clients. Do not publish
the loopback endpoint directly or treat its temporary bearer as an OAuth flow.

### Microsoft 365 Copilot

Use the [Agents Toolkit templates and steps](../integrations/microsoft365/README.md).
This route installs a declarative agent with an authenticated remote MCP action;
select it inside Copilot Chat. API-key authentication is not supported for this
MCP plugin route. Tenant policy and provisioning remain customer setup.

## Verify the whole conversation

Use the same account and evidence range for these steps:

1. Ask: “How did I prepare invoices last Tuesday?” Check a real search/trace call,
   date bounds, pagination and evidence references rather than a guessed answer.
2. Ask: “Which repeated steps might I automate?” Verify candidates are descriptive,
   modern authorized APIs are considered, and missing payload is not automatically
   called an automation blocker. Narrow dates if source limits are reached.
3. Explain a hidden rule: “Prepare a draft for totals below 500; above that ask the
   owner. Never send without my approval.” Review the complete procedure before
   confirming a save. Keep unknown exceptions as explicit questions.
4. Open a new conversation and ask for the saved procedure. Verify the same rules
   and revision return without interviewing you again. Correct one rule and verify
   stale revisions cannot overwrite it.
5. Sign in as a second person. Verify neither first-person evidence nor knowledge
   is returned, including when reusing a cursor from the first person's trace.
6. Revoke local access or the appropriate remote permission and retry. Confirm
   refusal, forget the reviewed workflow, and verify all its saved versions disappear.

`tests/test_conversation_context.py` exercises database and tool/protocol behavior,
including 25,000 newer events, scopes, HTTP authentication, OAuth metadata, actor
isolation, revision conflicts and conversation continuity. These are deterministic
contract checks, not an evaluation of an external model's understanding. The steps
above require actual host/tenant testing before claiming a seamless integration.
