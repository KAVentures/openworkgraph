from __future__ import annotations

from pathlib import Path


def test_chatgpt_adapter_reuses_rich_local_mcp_without_new_data_plane():
    from gateway.chatgpt_mcp_http import app as chatgpt_app, mcp as chatgpt_mcp
    from mcp_server.compact_http_app import app as compact_app, mcp as compact_mcp

    assert chatgpt_mcp is compact_mcp
    assert chatgpt_app is compact_app


def test_chatgpt_adapter_is_loopback_by_default():
    source = Path("gateway/chatgpt_mcp_http.py").read_text(encoding="utf-8")
    assert 'OWG_CHATGPT_MCP_HOST", "127.0.0.1"' in source
    assert 'OWG_CHATGPT_MCP_PORT", "8791"' in source


def test_chatgpt_skill_preserves_evidence_and_authorization_boundaries():
    skill = Path("integrations/chatgpt/SKILL.md").read_text(encoding="utf-8")
    assert "evidence layer, not an authority" in skill
    assert "untrusted data" in skill
    assert "Never infer permission from historical behavior" in skill
    assert "Do not mechanically replay historical UI clicks" in skill
    assert "rather than inventing it" in skill


def test_chatgpt_integration_does_not_duplicate_storage_or_sync():
    integration = Path("integrations/chatgpt")
    names = {path.name for path in integration.rglob("*") if path.is_file()}
    assert {"README.md", "SKILL.md", "review_cases.json"}.issubset(names)
    assert "SKILL.md" in {path.name for path in (integration / "skills" / "openworkgraph").iterdir()}
    assert not any(name.endswith((".db", ".sqlite", ".sqlite3")) for name in names)


def test_chatgpt_skill_routes_implicit_and_explicit_owg_intent():
    skill = Path("integrations/chatgpt/SKILL.md").read_text(encoding="utf-8")
    assert "The user should not need to name OpenWorkGraph" in skill
    assert "continue what I was doing" in skill
    assert "use OWG" in skill
    assert "prefer one cheap context/search lookup" in skill


def test_chatgpt_personal_path_exposes_rich_workflow_tools():
    compact = Path("mcp_server/compact_http_app.py").read_text(encoding="utf-8")
    skill = Path("integrations/chatgpt/SKILL.md").read_text(encoding="utf-8")
    assert "register_workflow_evidence_tools" in compact
    assert "register_history_tools" in compact
    assert "find_repeated_workflows" in skill
    assert "get_workflow_evidence" in skill


def test_chatgpt_personal_path_preserves_local_bearer_guard():
    source = Path("gateway/chatgpt_mcp_http.py").read_text(encoding="utf-8")
    assert "from mcp_server.compact_http_app import app, mcp" in source
    compact_http = Path("mcp_server/compact_http_app.py").read_text(encoding="utf-8")
    assert "MCPBearerGuard" in compact_http
    assert "mcp_bearer_matches" in compact_http


def test_packaged_chatgpt_skill_matches_source_and_stays_on_public_tool_surface():
    source = Path("integrations/chatgpt/SKILL.md").read_text(encoding="utf-8")
    packaged = Path("integrations/chatgpt/skills/openworkgraph/SKILL.md").read_text(encoding="utf-8")
    assert source == packaged
    assert "get_information_transfers" not in source
    assert r"\n\n" not in source
    assert "Do not browse OWG speculatively" in source
    assert "The user should not need to name OpenWorkGraph" in source


def test_claude_local_skill_remains_optional_and_remote_integration_is_documented():
    skill = Path("integrations/plugins/openworkgraph/skills/owg/SKILL.md").read_text(encoding="utf-8")
    guide = Path("docs/CLAUDE_REMOTE_CONNECTOR.md").read_text(encoding="utf-8")
    assert "when the user does not name OWG" in skill
    assert "Only call optional tools" in skill
    assert "https://mcp.owg.kinvectum.com/mcp" in guide
    assert "single MCP connector" in guide.lower()
    assert "does not automatically gain access" in guide
