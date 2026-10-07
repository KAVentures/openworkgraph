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


def test_public_rate_limit_keeps_discovery_open_and_stops_token_rotation():
    import asyncio
    import pytest
    pytest.importorskip("jwt")
    from gateway.public_plugin_mcp import PublicPluginRateLimit

    calls = []

    async def inner(scope, receive, send):
        calls.append(scope["path"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    app = PublicPluginRateLimit(inner, limit_per_minute=2)

    async def request(path="/mcp", authorization=None, ip="203.0.113.7"):
        messages = []
        headers = []
        if authorization:
            headers.append((b"authorization", authorization.encode()))
        scope = {"type": "http", "method": "POST", "path": path, "headers": headers, "client": (ip, 12345)}
        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}
        async def send(message):
            messages.append(message)
        await app(scope, receive, send)
        return next(item["status"] for item in messages if item["type"] == "http.response.start")

    assert asyncio.run(request(authorization="Bearer junk-1")) == 200
    assert asyncio.run(request(authorization="Bearer junk-2")) == 200
    # Rotating a third junk token cannot bypass the per-IP pre-authentication bucket.
    assert asyncio.run(request(authorization="Bearer junk-3")) == 429
    # Discovery is deliberately outside the request limiter so clients can reconnect.
    assert asyncio.run(request(path="/.well-known/oauth-protected-resource/mcp")) == 200
    # A different client IP has an independent pre-authentication bucket.
    assert asyncio.run(request(authorization="Bearer junk-4", ip="198.51.100.8")) == 200


def test_public_identity_fallback_uses_reserved_oauth_namespace(monkeypatch):
    import pytest
    pytest.importorskip("jwt")
    from gateway import public_plugin_mcp
    from mcp.server.auth.provider import AccessToken

    token = AccessToken(
        token="token",
        client_id="chatgpt",
        scopes=["work:read"],
        expires_at=4102444800,
        resource="https://mcp.example.com/mcp",
        subject="acme",
        claims={"sub": "acme"},
    )
    monkeypatch.setattr(public_plugin_mcp, "get_access_token", lambda: token)
    organization_id, actor_id, _claims = public_plugin_mcp._identity()
    assert organization_id == "oauth-sub:acme"
    assert actor_id == "oauth-sub:acme"

    partial = AccessToken(
        token="token-2",
        client_id="chatgpt",
        scopes=["work:read"],
        expires_at=4102444800,
        resource="https://mcp.example.com/mcp",
        subject="acme",
        claims={"sub": "acme", "owg_org_id": "org-only"},
    )
    monkeypatch.setattr(public_plugin_mcp, "get_access_token", lambda: partial)
    with pytest.raises(RuntimeError, match="both owg_org_id and owg_actor_id"):
        public_plugin_mcp._identity()


def test_public_agent_run_scan_has_bounded_default():
    source = Path("gateway/public_plugin_mcp.py").read_text(encoding="utf-8")
    assert "max_events: int = 5_000" in source
    assert "event_budget = max(100, min(int(max_events), 5_000))" in source
    assert "25_000 - len(raw)" not in source


def test_compact_empty_context_hint_names_live_tool():
    source = Path("mcp_server/compact.py").read_text(encoding="utf-8")
    assert '"tool": "search_work or get_workflow_trace with an authorized date range"' in source
    assert "search_work_history or get_workflow_trace" not in source


def test_context_pulse_has_explicit_read_only_annotations():
    source = Path("mcp_server/compact_hardening.py").read_text(encoding="utf-8")
    marker = "readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False"
    pulse = source[source.index("@compact_module.mcp.tool"):source.index("compact_module.get_context_pulse")]
    assert marker in pulse


def test_personal_link_endpoint_is_oauth_bound_and_not_an_mcp_tool():
    source = Path("gateway/public_plugin_mcp.py").read_text(encoding="utf-8")
    assert '"/v1/plugin/device-link"' in source
    assert 'organization_id = f"oauth-sub:{subject}"' in source
    assert '"personal device linking is only for unmapped personal OAuth accounts"' in source
    assert "create_enrollment_grant(" in source
    assert 'if not gateway_url.startswith("https://"):' in source
    assert '"personal device linking is not configured"' in source
    # Credential issuance stays outside the model-visible public tool surface.
    expected = {
        "get_profile", "get_current_work_context", "search_work",
        "get_workflow_trace", "find_repeated_workflows",
        "get_workflow_evidence", "get_agent_runs",
    }
    assert expected == {
        "get_profile", "get_current_work_context", "search_work",
        "get_workflow_trace", "find_repeated_workflows",
        "get_workflow_evidence", "get_agent_runs",
    }


def test_personal_enrollment_does_not_require_model_or_local_tenant_selection():
    enroll = Path("connector/enroll.py").read_text(encoding="utf-8")
    service = Path("connector/service.py").read_text(encoding="utf-8")
    assert 'parser.add_argument("--organization", default=""' in enroll
    assert 'raise ValueError("organization_id is required")' not in service[service.index("def enroll_endpoint"):service.index("def disconnect_endpoint")]
    assert "resolved_organization_id" in service
    assert '"history_sync_mode": "from_enrollment_forward"' in service
