# OpenWorkGraph cloud plugin for Claude

This package configures **one remote read-only MCP server** for Claude. It reuses `https://mcp.owg.kinvectum.com/mcp` (the ChatGPT cloud endpoint) and adds a skill for appropriate implicit work-history retrieval. It creates **no** new backend, database, API key, or local capture process.

## Claude chat, Desktop, Cowork and Claude Code

The simplest way to connect to the same data from Claude is **Customize → Connectors → Add → Custom → Web**, name it `OpenWorkGraph`, enter `https://mcp.owg.kinvectum.com/mcp`, and complete OAuth sign-in. Enable it for the conversation. Users of plugins/marketplaces can instead install **OpenWorkGraph (Cloud)** from this repository's marketplace, which bundles the same remote MCP URL and a work-continuity skill. Do not install both the custom connector and cloud plugin if this duplicates the same MCP tool set.

This cloud integration is different from `../openworkgraph`, the existing local Claude Code plugin. The local plugin's optional richer local tools, hooks and launch scripts remain unchanged. Choose the local plugin for local-only use; choose this cloud integration for synced, remotely accessible evidence in Claude chat and across devices.

## Authentication

The remote server advertises OAuth protected-resource metadata and uses the configured OWG Supabase OAuth issuer. Use Claude's published OAuth identity if compatible, otherwise automatic registration when enabled by the OWG authorization server. If registration is unavailable, a separately registered Claude OAuth client may be required. The callback URL for a registered Claude client is `https://claude.ai/api/mcp/auth_callback`. Never paste a bearer token or service-role key into the plugin files.

Sign in to the same OWG account linked by the desktop **Connect AI → Link and share new evidence** flow. Reconnect after changing OAuth settings. The cloud server only returns evidence explicitly synchronized under that account's scope; work text requires a separate opt-in.

## Smoke test

After authenticating, inspect the tool catalog. The current intended public cloud tool set is **ten read-only tools**: `get_profile`, `get_goal_context`, `get_current_work_context`, `search_work`, `get_workflow_trace`, `find_repeated_workflows`, `get_workflow_evidence`, `get_agent_runs`, `search_work_text`, and `get_work_text_excerpt`.

Try: **"Use my recorded work to find what I was doing earlier today."** Confirm an actual OWG tool invocation and sourced result, rather than accepting a plausible answer. For text: search a phrase known to be explicitly shared; only use `get_work_text_excerpt` when search returns a real reference. Run a negative control such as **"Explain the Pythagorean theorem"** and confirm OWG isn't needlessly invoked. Confirm disconnected/no-sync accounts return a safe empty/onboarding result. Do not claim success until tested in a real Claude session.

Detailed rollout and publication gates: [Claude remote connector guide](../../../docs/CLAUDE_REMOTE_CONNECTOR.md).
