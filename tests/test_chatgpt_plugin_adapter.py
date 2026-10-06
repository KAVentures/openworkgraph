from __future__ import annotations

from pathlib import Path


def test_chatgpt_adapter_reuses_gateway_mcp_without_new_data_plane():
    from gateway.chatgpt_mcp_http import mcp as chatgpt_mcp
    from gateway.mcp import mcp as gateway_mcp

    assert chatgpt_mcp is gateway_mcp


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
    assert names == {"README.md", "SKILL.md"}
