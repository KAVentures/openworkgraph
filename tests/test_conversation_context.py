from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from shared.workflow_knowledge import KnowledgeStore, KnowledgeWrite
from gateway.auth import Principal
from gateway.hardening import HardeningSettings
from gateway.human_access import HumanAccessSettings
from gateway.human_enterprise_app import create_human_enterprise_app
from gateway.settings import GatewaySettings


def test_historical_window_is_applied_before_newest_event_limit(monkeypatch):
    from server import procedural_memory, workflow_evidence
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE events(event_id TEXT, observed_at TEXT)")
    conn.executemany("INSERT INTO events VALUES (?, ?)", [("old-1", "2026-09-01T09:00:00+00:00"), ("old-2", "2026-09-01T09:01:00Z")] + [(f"new-{i}", "2026-10-01T09:00:00Z") for i in range(25000)])
    monkeypatch.setattr(procedural_memory, "rows", lambda sql, params=(): [dict(row) for row in conn.execute(sql, params)])
    events = workflow_evidence._load(limit=25000, since="2026-09-01T10:59:00+02:00", until="2026-09-02T00:00:00Z")
    assert [event["event_id"] for event in events] == ["old-1", "old-2"]
    assert procedural_memory.load_recent_evidence(until="2026-09-01T09:01:00Z") == [events[0]]
    with pytest.raises(ValueError):
        workflow_evidence._load(limit=10, since="2026-10-01T00:00:00Z", until="2026-09-01T00:00:00Z")


@pytest.mark.parametrize("mode", ["off", "selected_range", "all_saved"])
def test_repeated_discovery_uses_current_or_granted_dates(monkeypatch, mode):
    from mcp_server import compact
    from mcp_server.history_guard import install_history_guard
    calls = []
    def get(path, params=None):
        if path == "/v1/history/ai-access":
            return {"access": {"mode": mode, "since": "2026-09-01T00:00:00Z", "until": "2026-09-02T00:00:00Z"}}
        if path == "/v1/capture/status":
            return {"run_started_at": "2026-10-01T00:00:00Z"}
        calls.append((path, params))
        return {"families": [{"family_key": "family:invoice", "high_support_structural_steps": ["Excel", "Outlook"]}]}
    runtime = SimpleNamespace(secure_get=get)
    install_history_guard(runtime)
    monkeypatch.setattr(compact, "secure_runtime", runtime)
    monkeypatch.setattr(compact.core, "_begin", lambda name: None)
    monkeypatch.setattr(compact.core, "_finish", lambda name, result: result)
    result = compact.find_repeated_workflows(task_family="invoice")
    assert result["families"][0]["family_key"] == "family:invoice"
    assert [path for path, _ in calls] == ["/v1/workflow-evidence/families"]
    params = calls[0][1]
    if mode == "off":
        assert params["since"] == "2026-10-01T00:00:00Z"
    elif mode == "selected_range":
        assert params["since"].startswith("2026-09-01")
        assert params["until"].startswith("2026-09-02")


def test_reviewed_knowledge_mcp_boundary_preserves_intentional_instructions():
    from mcp_server.main import _finish, _finish_reviewed_knowledge
    procedure = "Use the browser tool to inspect the invoice, then call the approved accounting function. " + ("Keep this reviewed instruction exactly. " * 60)
    reviewed = _finish_reviewed_knowledge("test", {"procedure": procedure})
    assert reviewed["procedure"] == procedure
    assert reviewed["_openworkgraph_security"]["trust"] == "user_reviewed_workflow_knowledge"
    assert reviewed["_openworkgraph_security"]["content_preserved"] is True
    observed = _finish("test", {"procedure": procedure})
    assert observed["procedure"] != procedure
    assert observed["_openworkgraph_security"]["trust"] == "untrusted_observed_data"


def test_knowledge_requires_review_versions_isolates_and_forgets(tmp_path):
    from gateway.db import GatewayDB
    store = KnowledgeStore(GatewayDB(f"sqlite:///{tmp_path / 'knowledge.db'}"))
    record = KnowledgeWrite(workflow_id="invoice", title="Invoice review", procedure="Check totals before sending.", source_client="ChatGPT", decision_rules=["Ask the owner if totals differ."])
    with pytest.raises(ValueError, match="review"):
        store.save("alice", record)
    first = store.save("alice", record.model_copy(update={"user_confirmed": True}))
    assert first["revision"] == 1
    assert first["observed_evidence"] is first["execution_authorization"] is False
    with pytest.raises(ValueError, match="Revision"):
        store.save("alice", record.model_copy(update={"user_confirmed": True}))
    second = store.save("alice", record.model_copy(update={"user_confirmed": True, "expected_revision": 1, "procedure": "Check totals and currency."}))
    assert second["revision"] == 2
    assert second["content_sha256"] != first["content_sha256"]
    assert store.list("bob")["workflows"] == []
    assert store.list("alice")["workflows"] == [second]
    assert store.forget("bob", "invoice")["versions_deleted"] == 0
    assert store.forget("alice", "invoice")["versions_deleted"] == 2
    assert store.list("alice")["workflows"] == []


class Verifier:
    def verify(self, token):
        if token not in {"alice", "bob", "readonly"}:
            raise ValueError("invalid token")
        return {"sub": token, "actor": "alice" if token == "readonly" else token, "org": "company", "scp": "work.read", "groups": ["writers"] if token != "readonly" else []}


def remote_app(tmp_path):
    settings = GatewaySettings(database_url=f"sqlite:///{tmp_path / 'gateway.db'}", admin_token="admin", enrollment_token="enroll")
    human = HumanAccessSettings(issuer="https://identity.example", audience="openworkgraph", jwks_url="https://identity.example/keys", organization_claim="org", actor_claim="actor", mcp_resource_url="https://owg.example/mcp", mcp_oauth_scope="work.read", group_scope_map={"writers": frozenset({"self:knowledge:write"})})
    return create_human_enterprise_app(settings=settings, hardening=HardeningSettings(), human_access=human, verifier=Verifier())


def rpc(client, token, name, arguments=None, request_id=1):
    headers = {"Host": "owg.example", "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": request_id, "method": "tools/call", "params": {"name": name, "arguments": arguments or {}}})


def payload(response):
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert not result.get("isError"), result
    return result.get("structuredContent") or json.loads(result["content"][0]["text"])


def test_remote_mcp_auth_metadata_and_protocol(tmp_path):
    app = remote_app(tmp_path)
    with TestClient(app) as client:
        response = rpc(client, None, "get_current_work_context")
        assert response.status_code == 401
        assert "resource_metadata" in response.headers["www-authenticate"]
        assert rpc(client, "invalid", "get_current_work_context").status_code == 401
        metadata = client.get("/.well-known/oauth-protected-resource/mcp", headers={"Host": "owg.example"})
        assert metadata.status_code == 200, metadata.text
        assert metadata.json()["resource"] == "https://owg.example/mcp"
        assert metadata.json()["authorization_servers"] == ["https://identity.example"]
        headers = {"Host": "owg.example", "Authorization": "Bearer alice", "Accept": "application/json, text/event-stream"}
        initialized = client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "conversation-test", "version": "1"}}})
        assert initialized.status_code == 200, initialized.text
        assert "background work evidence" in initialized.json()["result"]["instructions"]
        tools = client.post("/mcp", headers=headers | {"MCP-Protocol-Version": "2025-11-25"}, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        assert {tool["name"] for tool in tools.json()["result"]["tools"]} >= {"search_work", "find_repeated_workflows", "get_workflow_evidence", "save_workflow_knowledge"}


def test_remote_person_scope_and_conversation_continuity(tmp_path):
    app = remote_app(tmp_path)
    with TestClient(app) as client:
        db = app.state.db
        for actor in ("alice", "bob"):
            p = Principal(token_id=actor, token_type="device", organization_id="company", actor_id=actor, device_id=actor, scopes=frozenset({"evidence:write"}))
            db.insert_events(p, [{"event_id": f"{actor}-{i}", "observed_at": f"2026-10-01T09:0{i}:00Z", "app": "Excel", "event_type": "focus_span", "session_id": actor, "window_title": actor, "duration_seconds": 60, "metadata": {}} for i in range(2)])
        first = payload(rpc(client, "alice", "get_workflow_trace", {"limit": 1}))
        assert first["rows"][0]["actor_id"] == "alice"
        # An explicit narrower end bound must override the broader snapshot in an older cursor.
        narrowed = payload(rpc(client, "alice", "get_workflow_trace", {"cursor": first["next_cursor"], "until": "2026-10-01T09:00:30Z"}))
        assert narrowed["rows"] == []
        # A cursor from another person cannot change the authenticated actor filter.
        second = payload(rpc(client, "bob", "get_workflow_trace", {"cursor": first["next_cursor"]}))
        assert all(row["actor_id"] == "bob" for row in second["rows"])
        reviewed_procedure = "Use the browser tool to inspect the invoice, then call the approved accounting function before drafting the email. " + ("Preserve this reviewed step exactly. " * 60)
        knowledge = {"workflow_id": "invoice", "title": "Invoice", "procedure": reviewed_procedure, "source_client": "ChatGPT", "user_confirmed": True, "decision_rules": ["Obtain owner approval before sending."], "evidence_refs": ["alice-0"]}
        denied = rpc(client, "readonly", "save_workflow_knowledge", {"record": knowledge}).json()["result"]
        assert denied["isError"]
        saved = payload(rpc(client, "alice", "save_workflow_knowledge", {"record": knowledge}))
        assert saved["revision"] == 1
        assert saved["procedure"] == reviewed_procedure
        assert saved["_openworkgraph_security"]["trust"] == "user_reviewed_workflow_knowledge"
        # A new chat retrieves the reviewed explanation without re-interviewing.
        restored = payload(rpc(client, "alice", "get_workflow_knowledge"))
        assert restored["workflows"][0]["decision_rules"] == knowledge["decision_rules"]
        assert restored["workflows"][0]["procedure"] == reviewed_procedure
        assert restored["_openworkgraph_security"]["content_preserved"] is True
        assert payload(rpc(client, "bob", "get_workflow_knowledge"))["workflows"] == []
        assert payload(rpc(client, "alice", "forget_workflow_knowledge", {"workflow_id": "invoice"}))["versions_deleted"] == 1
        assert payload(rpc(client, "alice", "get_workflow_knowledge"))["workflows"] == []


def test_old_local_cursor_cannot_restore_broader_history_grant(monkeypatch):
    from contextlib import contextmanager
    from server import mcp_trace
    from mcp_server.history_guard import install_history_guard
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE events(id INTEGER PRIMARY KEY, event_id TEXT, observed_at TEXT, app TEXT, window_title TEXT, event_type TEXT, metadata_json TEXT, session_id TEXT)")
    conn.executemany("INSERT INTO events VALUES (?, ?, ?, 'Excel', '', 'focus_span', '{}', 's')", [(i, f"e{i}", f"2026-09-0{i}T09:00:00+00:00") for i in range(1, 5)])
    @contextmanager
    def connect():
        yield conn
    monkeypatch.setattr(mcp_trace, "connect", connect)
    first = mcp_trace.workflow_trace(scope="all", limit=1, since="2026-09-01T00:00:00Z", until="2026-09-05T00:00:00Z")
    assert first["has_more"]
    def get(path, params=None):
        if path == "/v1/history/ai-access":
            return {"access": {"mode": "selected_range", "since": "2026-09-03T00:00:00Z", "until": "2026-09-04T00:00:00Z"}}
        return mcp_trace.workflow_trace(**params)
    runtime = SimpleNamespace(secure_get=get)
    install_history_guard(runtime)
    narrowed = runtime.secure_get("/v1/workflow-trace", {"scope": "current", "cursor": first["next_cursor"]})
    assert [row["event_id"] for row in narrowed["rows"]] == ["e3"]


def test_dedicated_tools_refuse_master_access_off():
    import asyncio
    from mcp.server import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError
    from mcp_server.workflow_evidence_tools import register_workflow_evidence_tools
    calls = []
    def denied(name):
        calls.append(name)
        raise ToolError("AI access is OFF")
    runtime = SimpleNamespace(authorize_tool=denied)
    mcp = MCPServer("test")
    register_workflow_evidence_tools(mcp, runtime)
    async def check():
        for name, args in [("get_workflow_evidence", {"family_key": "human:demo"}), ("get_workflow_knowledge", {}), ("save_workflow_knowledge", {"workflow_id": "demo", "title": "Demo", "procedure": "Prepare a draft.", "source_client": "test", "user_confirmed": True}), ("forget_workflow_knowledge", {"workflow_id": "demo"})]:
            with pytest.raises(ToolError, match="AI access is OFF"):
                await mcp.call_tool(name, args)
    asyncio.run(check())
    assert len(calls) == 4


def test_standing_history_access_survives_read_and_can_be_revoked(monkeypatch, tmp_path):
    from shared import history_policy
    monkeypatch.setattr(history_policy, "policy_path", lambda: tmp_path / "history-policy.json")
    history_policy.initialize_policy(has_existing_evidence=False)
    grant = history_policy.set_ai_history_access(mode="all_saved", expires_minutes=None)
    assert grant["expires_at"] is None
    assert history_policy.active_ai_history_access()["mode"] == "all_saved"
    history_policy.set_ai_history_access(mode="off", expires_minutes=None)
    assert history_policy.active_ai_history_access()["mode"] == "off"


def test_remote_verifier_rejects_tokens_without_delegated_scope(tmp_path):
    import asyncio
    from gateway.conversation_mcp import DelegatedTokenVerifier
    class IdentityOnly:
        def verify(self, token):
            return {"sub": "alice", "actor": "alice", "org": "company"}
    settings = HumanAccessSettings(mcp_oauth_scope="api://app/work.read", organization_claim="org", actor_claim="actor")
    assert asyncio.run(DelegatedTokenVerifier(settings, IdentityOnly()).verify_token("identity-token")) is None
    accepted = asyncio.run(DelegatedTokenVerifier(settings, Verifier()).verify_token("alice"))
    assert "api://app/work.read" in accepted.scopes
