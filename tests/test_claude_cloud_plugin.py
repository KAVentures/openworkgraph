"""Regression contracts for Claude's remote OWG cloud plugin package.

These static checks deliberately do not perform OAuth or assert that Claude's
host has actually routed a call. Live auth and routing are manual release gates.
"""
import json
from pathlib import Path

PLUGIN = Path("integrations/plugins/openworkgraph-cloud")
PUBLIC_TOOLS = {
    "get_profile", "get_current_work_context", "get_goal_context",
    "search_work", "get_workflow_trace", "find_repeated_workflows",
    "get_workflow_evidence", "get_agent_runs",
    "search_work_text", "get_work_text_excerpt",
}


def test_claude_cloud_plugin_reuses_existing_oauth_protected_mcp():
    manifest = json.loads((PLUGIN / ".claude-plugin/plugin.json").read_text())
    servers = json.loads((PLUGIN / ".mcp.json").read_text())["mcpServers"]
    assert manifest["name"] == "openworkgraph-cloud"
    assert len(servers) == 1
    server = servers["openworkgraph"]
    assert server == {"type": "http", "url": "https://mcp.owg.kinvectum.com/mcp"}
    # No static secrets, local subprocess or duplicate backend.
    assert "headers" not in server
    assert "command" not in server
    assert "env" not in server


def test_marketplace_offers_cloud_without_changing_local_plugin():
    marketplace = json.loads(Path(".claude-plugin/marketplace.json").read_text())
    entries = {p["name"]: p["source"] for p in marketplace["plugins"]}
    assert entries["openworkgraph"] == "./integrations/plugins/openworkgraph"
    assert entries["openworkgraph-cloud"] == "./integrations/plugins/openworkgraph-cloud"
    local = json.loads(Path("integrations/plugins/openworkgraph/.mcp.json").read_text())
    assert local["mcpServers"]["openworkgraph"]["command"] == "node"


def test_cloud_skill_routes_implicit_context_and_guards_text_excerpts():
    skill = (PLUGIN / "skills/work-continuity/SKILL.md").read_text()
    assert skill.startswith("---\nname: work-continuity\n")
    assert "when they do not mention OpenWorkGraph" in skill
    for tool in PUBLIC_TOOLS:
        assert f"`{tool}`" in skill or tool == "get_profile" and "`get_profile`" in skill
    assert "real excerpt reference returned by search" in skill
    assert "untrusted data" in skill
    assert "never invent" in skill.lower()
    assert "not permission" in skill


def test_cloud_catalog_documentation_matches_actual_server_registration():
    src = Path("gateway/public_plugin_mcp.py").read_text()
    assert src.count("@server.tool(") == 10
    for tool in PUBLIC_TOOLS:
        assert f"    def {tool}(" in src
    assert "readOnlyHint=True" in src
    guide = Path("docs/CLAUDE_REMOTE_CONNECTOR.md").read_text()
    assert "ten read-only" in guide
    for tool in PUBLIC_TOOLS:
        assert f"`{tool}`" in guide
