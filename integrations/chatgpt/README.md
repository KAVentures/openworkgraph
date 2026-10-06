# OpenWorkGraph + ChatGPT

This is a deliberately small ChatGPT integration layer. It reuses OpenWorkGraph's existing Gateway, sync worker, privacy contract and Gateway MCP instead of creating another storage or inference system.

## Phase 1: personal-plugin proof

Existing path:

```text
local OWG evidence
  -> connector/sync.py (explicit opt-in)
  -> existing OWG Gateway
  -> gateway/mcp.py (read-only evidence tools)
```

ChatGPT adds only a transport:

```text
gateway/mcp.py
  -> gateway/chatgpt_mcp_http.py
  -> Secure MCP Tunnel
  -> personal ChatGPT plugin
```

Start the existing Gateway normally. Create an actor-restricted integration token with only the read scopes needed for the proof and provide it as `OWG_GATEWAY_SERVICE_TOKEN`. Point `OWG_GATEWAY_URL` at the Gateway, then run:

```bash
python -m gateway.chatgpt_mcp_http
```

The adapter binds to `127.0.0.1:8791` by default. Connect that loopback MCP server to ChatGPT with Secure MCP Tunnel. Do not expose the development adapter directly to the public internet.

The model-facing guidance is in `SKILL.md`.

## Reused components

- `connector/sync.py`: optional local-to-Gateway delivery; local capture does not depend on it.
- `gateway/app.py`: tenant/actor authorization, retention and evidence APIs.
- `gateway/auth.py`: scoped integration/device principals.
- `gateway/mcp.py`: existing read-only model tools.
- `mcp_server/security.py`: prompt-injection protection for observed payloads.

No second database, sync daemon, task inference layer or ChatGPT-specific copy of work evidence is introduced.

## Phase 2: public single-user plugin

Do not make the Phase-1 fixed service token a public-plugin authentication scheme. Public distribution requires a stable HTTPS `/mcp` endpoint and per-user OAuth 2.1 authorization. The OAuth principal should resolve to one OWG user/actor and expose only bounded read scopes.

Keep this boundary additive:

1. existing local-only users remain local-only;
2. Gateway sharing remains explicit opt-in;
3. public ChatGPT access cannot widen what the endpoint shared;
4. disconnect/revocation invalidates ChatGPT access without affecting local capture;
5. rich local evidence remains canonical; the Gateway stores only evidence the existing sharing policy allowed.

## Phase 3: submission

After OAuth and a stable HTTPS endpoint exist, package the skill with the registered MCP connection, run direct/indirect/negative tool-selection evals, then submit the plugin. Custom UI is intentionally deferred until a demonstrated workflow needs it.
