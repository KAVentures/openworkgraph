"""Production API + compact stdio MCP acceptance for reviewed context reuse.

Uses temporary local stores and synthetic reviewed content. No model or
provider calls, and no monkeypatch of the production authorization boundary.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from server.local_auth import ensure_api_token
from test_ai_context_mcp_v092 import ROOT, _free_port, _wait, _stop, _text, _events


def test_persisted_reviewed_knowledge_discovery_and_task_context(tmp_path):
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    data, auth, config = tmp_path / "data", tmp_path / "auth", tmp_path / "config.json"
    token = ensure_api_token(directory=auth)
    now = datetime.now(timezone.utc)
    started = (now - timedelta(days=4)).isoformat()
    env = os.environ.copy()
    env.update({
        "WORKFLOW_OBSERVER_DATA": str(data),
        "WORKFLOW_OBSERVER_AUTH_DIR": str(auth),
        "WORKFLOW_OBSERVER_CONFIG": str(config),
        "WORKFLOW_OBSERVER_DASHBOARD_BOOTSTRAP": "knowledge-acceptance",
        "WORKFLOW_OBSERVER_RUN_STARTED_AT": started,
        "WORKFLOW_OBSERVER_API": base,
        "PYTHONPATH": str(ROOT),
        "OWG_OPEN_WORK_EXPERIMENTAL_TOOLS": "0",
    })
    api = subprocess.Popen(
        [sys.executable, "-m", "server.enterprise_runner", "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL,
    )
    headers = {"Authorization": f"Bearer {token}"}
    try:
        _wait(base + "/health", api)
        assert httpx.get(base + "/v1/workflow-knowledge", params={"after": ""}).status_code == 401
        login = httpx.post(base + "/v1/dashboard-session", json={"bootstrap": "knowledge-acceptance"})
        assert login.status_code == 200, login.text
        dashboard = {"Authorization": f"OWG-Session {login.json()['session']}"}
        retention = httpx.put(base + "/v1/history-policy", headers=headers,
            json={"human_mode": "forever", "agent_mode": "forever", "onboarding_complete": True})
        assert retention.status_code == 200, retention.text
        assert httpx.post(base + "/v1/events", headers=headers, json={"events": _events(now)}).status_code == 200
        assert httpx.post(base + "/v1/ai-access", headers=dashboard, json={"enabled": True}).json()["enabled"] is True
        lease = httpx.post(base + "/v1/history/ai-access", headers=headers,
            json={"mode": "all_saved", "expires_minutes": 60})
        assert lease.status_code == 200, lease.text
        marker = "QuartzConfidential"
        setting = httpx.post(base + "/v1/ai-context", headers=dashboard,
            json={"detail": "redacted", "always_redact": [marker]})
        assert setting.status_code == 200, setting.text

        def save(workflow_id, title, procedure, revision=0):
            r = httpx.post(base + "/v1/workflow-knowledge", headers=dashboard, json={
                "workflow_id": workflow_id, "title": title, "procedure": procedure,
                "source_client": "acceptance", "user_confirmed": True, "expected_revision": revision,
                "unresolved_questions": ["Confirm approval owner"],
            })
            assert r.status_code == 200, r.text
            return r.json()

        first = save("proc_001", "Invoice review", "Inspect invoice marker " + marker)
        save("proc_002", "Renewal review", "Review renewal")
        last = save("proc_003", "Invoice close", "Check invoice closure")
        assert first["revision"] == last["revision"] == 1

        async def exercise():
            params = StdioServerParameters(command=sys.executable,
                args=["-m", "mcp_server.compact_stdio"], cwd=str(ROOT), env=env)
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    names = {t.name for t in (await session.list_tools()).tools}
                    assert {"get_task_context", "get_workflow_knowledge"} <= names

                    async def call(name, args, *, error=False):
                        result = await session.call_tool(name, args)
                        assert bool(result.is_error) == error, (name, _text(result)[:800])
                        if error:
                            return
                        assert isinstance(result.structured_content, dict), _text(result)[:800]
                        assert marker not in _text(result)
                        return result.structured_content

                    hidden = await call("get_workflow_knowledge", {"query": marker, "scan_limit": 1})
                    assert hidden["candidates"] == [] and hidden["next_after"] == "proc_001"
                    p1 = await call("get_workflow_knowledge", {"query": "invoice", "limit": 1, "scan_limit": 1})
                    assert p1["detail_level"] == "redacted"
                    assert [r["workflow_id"] for r in p1["candidates"]] == ["proc_001"]
                    p2 = await call("get_workflow_knowledge", {"query": "invoice", "after": p1["next_after"], "scan_limit": 1})
                    assert p2["candidates"] == [] and p2["next_after"] == "proc_002"
                    p3 = await call("get_workflow_knowledge", {"query": "invoice", "after": p2["next_after"], "scan_limit": 1})
                    assert [r["workflow_id"] for r in p3["candidates"]] == ["proc_003"]
                    assert not p3["has_more"]
                    ref = {k: p1["candidates"][0][k] for k in ("workflow_id", "revision", "content_sha256")}
                    exact = await call("get_workflow_knowledge", {"workflow_id": ref["workflow_id"]})
                    record = exact["workflows"][0]
                    assert {k: record[k] for k in ref} == ref
                    assert record["unresolved_questions"] == ["Confirm approval owner"]
                    context = await call("get_task_context", {"task_family": "email.reply", "knowledge_refs": [ref]})
                    companion = context["reviewed_knowledge_companion"]
                    assert companion["records"][0]["reviewed_record"] == record
                    assert companion["_openworkgraph_security"]["trust"] == "user_reviewed_workflow_knowledge"
                    assert companion["content_digest_verified"] is False
                    assert companion["execution_authorized"] is False
                    assert context["_openworkgraph_task_context_provenance"]["model_context_use_attested"] is False
                    # Default-off optional case context stays off even when the ordinary
                    # task context and reviewed knowledge tools are allowed.
                    await call("get_task_context", {"task_family": "email.reply", "case_record_ref": "owg:r:" + "a"*32}, error=True)
                    updated = save("proc_001", "Invoice review", "Inspect updated invoice", revision=1)
                    assert updated["revision"] == 2
                    await call("get_task_context", {"task_family": "email.reply", "knowledge_refs": [ref]}, error=True)
                    current = await call("get_workflow_knowledge", {"workflow_id": "proc_001"})
                    assert current["workflows"][0]["revision"] == 2
                    # Revocation must apply to the existing live MCP session.
                    off = httpx.post(base + "/v1/ai-access", headers=dashboard, json={"enabled": False})
                    assert off.status_code == 200 and off.json()["enabled"] is False
                    await call("get_workflow_knowledge", {"summary_only": True}, error=True)

        asyncio.run(exercise())
        # The AI-visible transformation did not mutate the reviewed local record.
        stored = httpx.get(base + "/v1/workflow-knowledge", headers=dashboard, params={"workflow_id": "proc_001"})
        assert stored.status_code == 200 and stored.json()["workflows"][0]["revision"] == 2
    finally:
        _stop(api)
