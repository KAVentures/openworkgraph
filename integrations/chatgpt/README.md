# OpenWorkGraph + ChatGPT

This is a deliberately small ChatGPT integration layer. It reuses OpenWorkGraph's existing Gateway, sync worker, privacy contract and Gateway MCP instead of creating another storage or inference system.

## Phase 1: personal-plugin proof

For the personal plugin, keep the evidence local and expose the existing rich compact MCP through Secure MCP Tunnel:

```text
local OWG evidence
  -> local OWG service + AI-access policy
  -> mcp_server/compact_http_app.py
  -> gateway/chatgpt_mcp_http.py (transport alias only)
  -> Secure MCP Tunnel
  -> personal ChatGPT plugin
```

This matters because the compact MCP already exposes the orientation, repeated-work, selected-execution evidence and agent-run tools used by OWG's local AI integrations. The personal ChatGPT path must not silently downgrade to the Gateway's smaller raw-history surface.

Start OpenWorkGraph normally so its local authenticated service is available, then run:

```bash
python -m gateway.chatgpt_mcp_http
```

The adapter binds to `127.0.0.1:8791` by default and preserves the compact MCP bearer guard. Connect that loopback MCP server to ChatGPT with Secure MCP Tunnel using the local MCP credential. Do not expose the development adapter directly to the public internet.

The model-facing guidance is in `SKILL.md`.

## Reused components

- `mcp_server/compact_http_app.py`: existing authenticated rich local MCP.
- `mcp_server/compact.py`: continuity/orientation and compact evidence tools.
- `mcp_server/workflow_evidence_tools.py`: repeated-work and selected-execution evidence bridge.
- `mcp_server/security.py`: prompt-injection protection for observed payloads.
- `connector/sync.py` and the Gateway remain optional and are not required for the personal-plugin proof.

No second database, sync daemon, task inference layer or ChatGPT-specific copy of work evidence is introduced.

## Phase 2: public single-user plugin

The public/remote plugin can use the Gateway path, but it must reach functional parity with the local compact evidence workflow before publication. Do not make a fixed service token a public-plugin authentication scheme. Public distribution requires a stable HTTPS `/mcp` endpoint and per-user OAuth 2.1 authorization. The OAuth principal should resolve to one OWG user/actor and expose only bounded read scopes.

Keep this boundary additive:

1. existing local-only users remain local-only;
2. Gateway sharing remains explicit opt-in;
3. public ChatGPT access cannot widen what the endpoint shared;
4. disconnect/revocation invalidates ChatGPT access without affecting local capture;
5. rich local evidence remains canonical; the Gateway stores only evidence the existing sharing policy allowed.

## Phase 3: submission

After OAuth and a stable HTTPS endpoint exist, package the skill with the registered MCP connection, run direct/indirect/negative tool-selection evals, then submit the plugin. Custom UI is intentionally deferred until a demonstrated workflow needs it.
