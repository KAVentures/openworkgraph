from __future__ import annotations

import json
from pathlib import Path
import zipfile


def test_review_set_has_exact_submission_case_counts():
    payload = json.loads(Path("integrations/chatgpt/review_cases.json").read_text(encoding="utf-8"))
    assert len(payload["positive"]) == 5
    assert len(payload["negative"]) == 3
    assert all(row.get("tools_triggered") and row.get("expected_behavior") for row in payload["positive"])
    assert all(not row.get("tools_triggered") and row.get("expected_behavior") for row in payload["negative"])


def test_public_plugin_builder_rejects_non_https(tmp_path):
    from scripts.build_chatgpt_plugin import build
    logo = tmp_path / "logo.svg"
    logo.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"></svg>', encoding="utf-8")
    try:
        build(
            mcp_url="http://127.0.0.1:8791/mcp",
            homepage="https://example.com",
            privacy_url="https://example.com/privacy",
            company_url="https://example.com",
            support_url="https://example.com/support",
            terms_url="https://example.com/terms",
            demo_recording_url="https://example.com/demo",
            logo=logo,
            countries=["SE"],
            developer_name="Verified Publisher",
            version="1.0.0",
            output=tmp_path / "plugin.zip",
        )
    except SystemExit as exc:
        assert "public HTTPS" in str(exc)
    else:
        raise AssertionError("loopback/non-HTTPS submission URL must be rejected")


def test_public_plugin_builder_emits_portable_package(tmp_path):
    from scripts.build_chatgpt_plugin import build
    logo = tmp_path / "logo.svg"
    logo.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"></svg>', encoding="utf-8")

    output = build(
        mcp_url="https://mcp.example.com/mcp",
        homepage="https://example.com/openworkgraph",
        privacy_url="https://example.com/privacy",
        company_url="https://example.com",
        support_url="https://example.com/support",
        terms_url="https://example.com/terms",
        demo_recording_url="https://example.com/demo",
        logo=logo,
        countries=["SE", "US"],
        developer_name="Verified Publisher",
        version="1.0.0",
        output=tmp_path / "openworkgraph.zip",
    )
    with zipfile.ZipFile(output) as archive:
        names = set(archive.namelist())
        assert {"plugin.json", "mcp.json", "skills/openworkgraph/SKILL.md", "review_cases.json", "assets/logo.svg"} <= names
        plugin = json.loads(archive.read("plugin.json"))
        mcp = json.loads(archive.read("mcp.json"))
    assert plugin["$schema"] == "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
    assert plugin["extensions"]["com.openai"]["interface"]["capabilities"] == ["Read"]
    assert plugin["author"]["name"] == "Verified Publisher"
    assert plugin["extensions"]["com.openai"]["interface"]["developerName"] == "Verified Publisher"
    assert len(plugin["extensions"]["com.openai"]["interface"]["displayName"]) <= 30
    assert len(plugin["extensions"]["com.openai"]["interface"]["shortDescription"]) <= 30
    assert plugin["extensions"]["com.openai"]["interface"]["privacyPolicyURL"].startswith("https://")
    assert plugin["extensions"]["com.openai"]["interface"]["supportURL"].startswith("https://")
    assert plugin["extensions"]["com.openai"]["interface"]["termsOfServiceURL"].startswith("https://")
    assert len(plugin["extensions"]["com.openai"]["interface"]["defaultPrompt"]) == 3
    assert len(plugin["extensions"]["com.openai"]["review"]["test_cases"]["positive"]) == 5
    assert len(plugin["extensions"]["com.openai"]["review"]["test_cases"]["negative"]) == 3
    assert plugin["extensions"]["com.openai"]["publication"]["countries"] == ["SE", "US"]
    assert mcp["mcpServers"]["openworkgraph"] == {
        "type": "streamable-http",
        "url": "https://mcp.example.com/mcp",
    }


def test_plugin_read_tools_have_explicit_review_annotations():
    compact = Path("mcp_server/compact.py").read_text(encoding="utf-8")
    gateway = Path("gateway/mcp.py").read_text(encoding="utf-8")
    history = Path("mcp_server/history_tools.py").read_text(encoding="utf-8")
    evidence = Path("mcp_server/workflow_evidence_tools.py").read_text(encoding="utf-8")
    marker = "ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)"
    assert marker in compact
    assert marker in gateway
    assert marker in history
    assert evidence.count(marker) >= 2
    assert "readOnlyHint=False, destructiveHint=True" in evidence


def test_gateway_public_path_reuses_same_workflow_projection():
    source = Path("gateway/workflow_evidence.py").read_text(encoding="utf-8")
    assert "list_workflow_evidence_candidates" in source
    assert "build_workflow_evidence" in source
    assert "_raw_events=raw" in source
    assert "gateway_synced_privacy_hardened_evidence" in source


def test_public_gateway_mcp_exposes_workflow_discovery_and_evidence():
    source = Path("gateway/mcp.py").read_text(encoding="utf-8")
    assert "def find_repeated_workflows(" in source
    assert "def get_workflow_evidence(" in source
    assert "/v1/workflow-evidence/families" in source
    assert "/v1/workflow-evidence" in source


def test_gitignore_excludes_egg_info():
    assert "*.egg-info/" in Path(".gitignore").read_text(encoding="utf-8")


def test_public_plugin_server_fails_closed_without_oauth_config(monkeypatch):
    import pytest
    pytest.importorskip("jwt")
    from gateway import public_plugin_mcp

    for key in ("OWG_PLUGIN_OAUTH_ISSUER", "OWG_PLUGIN_RESOURCE_URL", "OWG_PLUGIN_OAUTH_JWKS_URL"):
        monkeypatch.delenv(key, raising=False)
    try:
        public_plugin_mcp.create_public_mcp()
    except RuntimeError as exc:
        assert "OWG_PLUGIN_OAUTH_ISSUER" in str(exc)
    else:
        raise AssertionError("public MCP must not start without OAuth configuration")


def test_public_token_verifier_requires_work_read_scope(monkeypatch):
    import asyncio
    import pytest
    pytest.importorskip("jwt")
    from gateway import public_plugin_mcp

    verifier = public_plugin_mcp.OIDCJWTVerifier(
        issuer="https://auth.example.com",
        audience="https://mcp.example.com/mcp",
        jwks_url="https://auth.example.com/jwks",
        algorithms=["RS256"],
    )

    class Key:
        key = "public-key"

    monkeypatch.setattr(verifier.jwks, "get_signing_key_from_jwt", lambda token: Key())
    monkeypatch.setattr(public_plugin_mcp.jwt, "decode", lambda *args, **kwargs: {
        "sub": "user-1", "exp": 4102444800, "scope": "openid"
    })
    assert asyncio.run(verifier.verify_token("token")) is None

    monkeypatch.setattr(public_plugin_mcp.jwt, "decode", lambda *args, **kwargs: {
        "sub": "user-1", "exp": 4102444800, "scope": "openid work:read",
        "owg_org_id": "org-1", "owg_actor_id": "actor-1",
    })
    token = asyncio.run(verifier.verify_token("token"))
    assert token is not None
    assert token.subject == "user-1"
    assert "work:read" in token.scopes
    assert token.claims["owg_org_id"] == "org-1"


def test_public_mcp_never_accepts_model_supplied_identity():
    source = Path("gateway/public_plugin_mcp.py").read_text(encoding="utf-8")
    for signature in (
        "def get_current_work_context(limit:",
        "def search_work(query:",
        "def get_workflow_trace(",
        "def find_repeated_workflows(",
        "def get_workflow_evidence(",
        "def get_agent_runs(",
    ):
        assert signature in source
    assert "def get_current_work_context(actor_id" not in source
    assert "def search_work(query: str, actor_id" not in source
    assert "owg_actor_id" in source
    assert "get_access_token()" in source


def test_public_plugin_surface_is_focused_read_only_and_verifiable():
    source = Path("gateway/public_plugin_mcp.py").read_text(encoding="utf-8")
    expected = {
        "get_profile", "get_current_work_context", "search_work", "get_workflow_trace",
        "find_repeated_workflows", "get_workflow_evidence", "get_agent_runs",
    }
    registered = set()
    lines = source.splitlines()
    for index, line in enumerate(lines):
        if "@server.tool" not in line:
            continue
        for later in lines[index + 1:index + 8]:
            stripped = later.strip()
            if stripped.startswith("def "):
                registered.add(stripped.split("def ", 1)[1].split("(", 1)[0])
                break
    assert registered == expected
    assert "readOnlyHint=True" in source
    assert "destructiveHint=False" in source
    assert '"work:read"' in source
    assert "openai/profile" in source
    assert "/.well-known/openai-apps-challenge" in source
    assert "save_workflow_knowledge" not in source
    assert "forget_workflow_knowledge" not in source
