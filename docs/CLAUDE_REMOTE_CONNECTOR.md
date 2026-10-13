# Claude cloud connector and plugin for OpenWorkGraph

## Architecture: one hosted MCP, both clients

Use the same **OAuth-protected, read-only** MCP endpoint already configured for ChatGPT:

`https://mcp.owg.kinvectum.com/mcp`

Claude chat, Claude Desktop, Cowork and Claude Code can connect to the public endpoint. There is **no second backend** or second evidence store. Both clients use the OWG account identity and receive only explicitly synchronized and appropriately redacted Gateway evidence. Local-only activity is not remotely available.

The existing `integrations/plugins/openworkgraph` plugin launches a **local Claude Code** server with different capabilities. It **does not automatically gain access** to cloud work, and cloud Claude **does not automatically gain access** to locally retained data. Do not delete or replace the local plugin. The new `integrations/plugins/openworkgraph-cloud` option is for the remote, shared ChatGPT/Claude endpoint.

## Install right now: Claude custom connector (no merge required)

1. Open Claude and go to **Customize → Connectors → Add → Custom → Web** (UI wording may vary).
2. Name it **OpenWorkGraph**. Paste **`https://mcp.owg.kinvectum.com/mcp`**.
3. Review detected OAuth authentication. Choose **Sign in now** and prefer **Use Claude's published identity** if the OWG OAuth server accepts it. Otherwise use **Register automatically** if dynamic client registration is enabled. If neither is accepted, register a dedicated Claude OAuth client with callback `https://claude.ai/api/mcp/auth_callback` at the OWG authorization server; do not weaken token validation or share a secret in the repository.
4. Sign into the **same OWG account** used in desktop **Connect AI → Link and share new evidence**. Work recorded only locally is not visible until the user explicitly enables sync and links the device.
5. In a Claude conversation, enable OWG under **Connectors**; tool access **Auto** is a sensible default. Check that the correct ten tools appear after connection. The user decides whether cloud work text is separately enabled.
6. Test **"Based on my prior recorded work, what should I continue today?"** and observe an actual OWG tool call. A fluent answer without a tool call is **not** a successful routing test.

Official source: [Claude custom web connectors](https://support.claude.com/en/articles/11176164-use-connectors-to-extend-claude-s-capabilities). Claude supports cloud MCP using Streamable HTTP and OAuth. A custom connector is private until submitted to Anthropic's directory.

## Optional Claude plugin package / marketplace

The repository also includes the `openworkgraph-cloud` package. It packages the same remote server (no token/header or local launcher) plus a **work-continuity skill**, for users who prefer a plugin rather than a plain web connector.

- Marketplace source: `https://github.com/KAVentures/openworkgraph`
- Plugin entry: **`openworkgraph-cloud`** (display name **OpenWorkGraph (Cloud)**)
- Remote MCP server: `https://mcp.owg.kinvectum.com/mcp`
- Skill: `integrations/plugins/openworkgraph-cloud/skills/work-continuity/SKILL.md`

In Claude's **Customize → Plugins**, use **Add marketplace** to add the repository when your client supports personal GitHub marketplaces, then install **OpenWorkGraph (Cloud)**. In Claude Code, use `/plugin marketplace add KAVentures/openworkgraph` followed by `/plugin install openworkgraph-cloud@openworkgraph`; authenticate through Claude's MCP flow and inspect `/mcp`. Exact support varies by Claude surface and account. **Do not enable both** this remote package and a separate OWG web connector if that duplicates the same tools.

The repository retains the original `openworkgraph` local Claude Code plugin entry for local-only installs. A single mcp connector is sufficient to access the hosted ten-tool catalog; the package adds prompting/installation convenience, not another backend.

## Current cloud tool catalog (ten read-only tools)

| Tool | Use |
| --- | --- |
| `get_profile` | Verify current OWG identity, not work history |
| `get_goal_context` | Find prior human/agent work relevant to a new goal |
| `get_current_work_context` | Orient on recent recorded work |
| `search_work` | Find named historical work/resources |
| `get_workflow_trace` | Verify chronological evidence with bounds/pagination |
| `find_repeated_workflows` | Locate candidate recurring processes |
| `get_workflow_evidence` | Verify representative executions |
| `get_agent_runs` | Reconstruct previously observed agent attempts |
| `search_work_text` | Search separately opted-in redacted cloud work text |
| `get_work_text_excerpt` | Read a real reference returned by search, only if authorized |

This is the **intended catalog in source**, not a claim about what Claude has already authenticated to or discovered in production. All ten are marked read-only. Work-text tools are permission-gated and may return no data. Do not fabricate an excerpt reference if search finds none. The `get_profile` tool has OpenAI-specific profile metadata for ChatGPT, which other MCP clients can ignore; the shared OAuth resource remains client-neutral.

## OAuth and security gates

The OWG remote server advertises MCP protected-resource metadata and uses an external authorization server (currently Supabase OAuth). Confirm in the **actual target project**:

- OAuth 2.1 authorization-code + PKCE S256 flow is enabled and its discovery metadata resolves;
- Claude can register with DCR or use a published/pre-registered client (if pre-registered, the exact callback is `https://claude.ai/api/mcp/auth_callback`);
- issuer, audience, bearer signature, expiry, user identity and server-required scope validate; tokens refresh and revocation works;
- no missing or stale redirect URI, scope or audience produces an authentication loop;
- account sync, tenant isolation, redaction and separate work-text opt-in are preserved.

The hosted server must remain identity-scoped and read-only. **Never** use ChatGPT or Claude client names as user/tenant identifiers or treat observed work as permission to take future action. Do not commit tokens, user evidence or secrets.


## Evidence quality and compact output

For newer clients, `get_goal_context`, `get_current_work_context`, `search_work`, `get_workflow_trace` and `get_workflow_evidence` also accept `detail="minimal"` to reduce repetitive contract text without removing events or pagination. The existing `compact` default and `rich` detail remain supported.

Hosted `find_repeated_workflows` now returns `cluster_quality` and filters weak click-only clusters by default; `include_low_specificity=true` restores them for auditing. For truncated `get_workflow_evidence` excerpts, follow each `trace_lookup` using paged `get_workflow_trace`. Structural surface IDs are paired with a best-effort `surface_labels` dictionary. The profile and all model-facing observed strings follow one owner-identity pseudonymization policy, including first-name suffixes: no raw profile email. See [Hosted evidence presentation and safety contract](HOSTED_EVIDENCE_PRESENTATION.md).

## Release smoke test / comparison

These tests must be performed against an **isolated test user/tenant**, first in a real Claude session and then in ChatGPT against the same synthetic data:

1. OAuth discovery, consent, refresh, reconnection and sign-out/revocation; reject unauthenticated/invalid/expired tokens.
2. Confirm **all ten tools**, argument schemas, read-only annotations and valid responses through authenticated `tools/list` and representative `tools/call` requests.
3. Test an unlinked/empty account; ensure no false historical claims.
4. Run positive prompts for resuming work, past files, repeated procedures and earlier agent attempts. Count real tool invocations; verify retrieved evidence.
5. Run negative prompts (general knowledge, no personal-work dependency); avoid speculative OWG retrieval.
6. With text sharing off, `search_work_text` and `get_work_text_excerpt` must not leak content. When on, validate a real search-returned reference, owner scoping, bounded excerpt and revocation.
7. Verify historical text is treated as untrusted data, not executable instructions.

The repository tests validate local catalog/security invariants and packaging. **They do not validate Claude's actual tool routing or OAuth handshake**. Record live results separately; installation alone is not an end-to-end pass. Successful installation does not force automatic use of OWG when the user does not name it.

## Public directory submission

Anthropic's developer portal offers two publication routes: **single MCP connector** (recommended first—submit the hosted MCP URL) or a **GitHub plugin bundle** (host MCP + skills together). Directory approval and discovery are distinct from adding a personal connector. See [Build plugins for Claude](https://claude.com/blog/build-plugins-for-claude).

Submit only after a live Claude OAuth/read-only/privacy test with synthetic evidence and a reviewer account, plus verified terms/privacy/support pages and any required publisher review. Avoid exposing local sensor data or an MCP localhost URL to the directory. Claude's choice to invoke a connector cannot be forced by the server, a plugin skill, or AI SEO.
