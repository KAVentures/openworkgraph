from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from server.local_auth import ensure_api_token

ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait(url: str, process: subprocess.Popen) -> None:
    deadline = time.time() + 15
    while time.time() < deadline:
        if process.poll() is not None:
            break
        try:
            if httpx.get(url, timeout=0.4).status_code < 500:
                return
        except Exception:
            time.sleep(0.08)
    raise AssertionError("secure API did not start")


def _stop(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
        try: process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait(timeout=5)


def test_real_stdio_mcp_lists_tools_denies_then_reads_when_enabled(tmp_path):
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    data = tmp_path / "data"; auth = tmp_path / "auth"
    token = ensure_api_token(directory=auth)
    env = os.environ.copy()
    env.update({
        "WORKFLOW_OBSERVER_DATA": str(data),
        "WORKFLOW_OBSERVER_AUTH_DIR": str(auth),
        "WORKFLOW_OBSERVER_DASHBOARD_BOOTSTRAP": "stdio-test",
        "WORKFLOW_OBSERVER_RUN_STARTED_AT": "2026-09-18T15:00:00+00:00",
        "WORKFLOW_OBSERVER_API": base,
        "PYTHONPATH": str(ROOT),
    })
    api = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "server.secure_app:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    _wait(base + "/health", api)
    headers = {"Authorization": f"Bearer {token}"}
    event = {
        "event_id": "stdio-real-1", "observed_at": "2026-09-18T16:00:00+00:00",
        "device_id": "d", "sensor_id": "browser:test", "source": "browser_extension",
        "session_id": "s", "app": "Fortnox",
        "window_title": "Leverantörsfaktura 2026-4435 – Northwind Industries AB",
        "event_type": "browser_click", "duration_seconds": 0.2,
        "metadata": {"action": "click", "target": {"role": "button", "label": "Attestera faktura"}, "page": {"hostname": "fortnox.se", "pathname": "/invoices"}},
    }
    assert httpx.post(base + "/v1/events", json={"events": [event]}, headers=headers).status_code == 200

    async def exercise() -> None:
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "mcp_server.secure_stdio"],
            cwd=str(ROOT),
            env={
                "PYTHONPATH": str(ROOT),
                "WORKFLOW_OBSERVER_API": base,
                "WORKFLOW_OBSERVER_AUTH_DIR": str(auth),
                "WORKFLOW_OBSERVER_DATA": str(data),
                "WORKFLOW_OBSERVER_RUN_STARTED_AT": "2026-09-18T15:00:00+00:00",
            },
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listed = await session.list_tools()
                names = {tool.name for tool in listed.tools}
                expected_procedural = {
                    "get_procedural_memory",
                    "get_similar_runs",
                    "get_failure_patterns",
                    "get_next_likely_steps",
                    "get_approval_patterns",
                    "get_procedural_context_pack",
                    "get_governed_context_pack",
                    "get_task_context",
                    "get_action_policy_advisory",
                }
                assert "get_workflow_trace" in names
                assert "automation_candidates" in names
                assert "get_work_profile" in names
                expected_agent_inspection = {
                    "get_agent_runs",
                    "get_agent_execution_trace",
                }
                assert expected_procedural <= names
                assert expected_agent_inspection <= names
                assert len(names) == 24

                denied = await session.call_tool("get_workflow_trace", {"limit": 10})
                assert denied.is_error is True
                assert "AI access is OFF" in " ".join(getattr(x, "text", "") for x in denied.content)

                enabled = httpx.post(base + "/v1/ai-access", json={"enabled": True}, headers=headers)
                assert enabled.status_code == 200 and enabled.json()["enabled"] is True

                result = await session.call_tool("get_workflow_trace", {"limit": 10})
                assert result.is_error is False
                structured = result.structured_content or {}
                assert structured.get("returned") == 1
                rows = structured.get("rows") or []
                assert rows and "Northwind Industries AB" in rows[0]["window_title"]
                assert rows[0]["target_label"] == "Attestera faktura"

                memory = await session.call_tool("get_procedural_memory", {"max_events": 100})
                assert memory.is_error is False
                memory_structured = memory.structured_content or {}
                assert memory_structured.get("derived") is True
                assert memory_structured.get("authoritative") is False
                assert memory_structured.get("memory_is_regeneratable") is True

                pack = await session.call_tool(
                    "get_procedural_context_pack",
                    {"family_key": "human:github.create_issue", "max_events": 100},
                )
                assert pack.is_error is False
                pack_structured = pack.structured_content or {}
                assert pack_structured.get("derived") is True
                assert pack_structured.get("authoritative") is False
                assert pack_structured.get("prescriptive") is False
                assert (pack_structured.get("authority") or {}).get("policy_status") == "not_provided"
                assert pack_structured.get("memory_is_regeneratable") is True

                governed = await session.call_tool(
                    "get_governed_context_pack",
                    {"family_key": "human:github.create_issue", "max_events": 100},
                )
                assert governed.is_error is False
                governed_structured = governed.structured_content or {}
                assert governed_structured.get("declared_policy_status") == "not_declared"
                assert governed_structured.get("declared_policy") is None
                authority = governed_structured.get("authority_separation") or {}
                assert authority.get("observed_behavior_is_policy") is False
                assert authority.get("policy_inferred_from_behavior") is False
                assert authority.get("automatic_enforcement") is False

                task_context = await session.call_tool(
                    "get_task_context",
                    {"task_family": "github.create_issue", "max_events": 100},
                )
                assert task_context.is_error is False
                task_structured = task_context.structured_content or {}
                assert (task_structured.get("resolution") or {}).get("status") == "resolved"
                assert (task_structured.get("resolution") or {}).get("family_key") == "human:github.create_issue"
                assert task_structured.get("read_only") is True
                assert task_structured.get("writes_performed") is False
                assert (task_structured.get("authority_model") or {}).get("observed_behavior_becomes_policy") is False

                advisory = await session.call_tool(
                    "get_action_policy_advisory",
                    {
                        "family_key": "human:github.create_issue",
                        "proposed_step": "tool:deployment:tool:aaaaaaaaaaaa",
                    },
                )
                assert advisory.is_error is False
                advisory_structured = advisory.structured_content or {}
                assert advisory_structured.get("advisory_status") == "no_active_declared_policy"
                assert advisory_structured.get("authorization_decision") == "not_made"
                assert advisory_structured.get("action_allowed") is None
                assert advisory_structured.get("automatic_enforcement") is False
                assert advisory_structured.get("execution_performed") is False
                assert advisory_structured.get("writes_performed") is False

                resources = await session.list_resources()
                uris = {str(r.uri) for r in resources.resources}
                assert "openworkgraph://ai-guide" in uris

    try:
        asyncio.run(exercise())
        activity = httpx.get(base + "/v1/mcp-activity", headers=headers).json()["items"]
        assert any(x["tool"] == "get_workflow_trace" and x["status"] == "ok" for x in activity)
        assert any(x["tool"] == "get_workflow_trace" and x["status"] == "denied" for x in activity)
        assert any(x["tool"] == "get_procedural_memory" and x["status"] == "ok" for x in activity)
        assert any(x["tool"] == "get_procedural_context_pack" and x["status"] == "ok" for x in activity)
        assert any(x["tool"] == "get_governed_context_pack" and x["status"] == "ok" for x in activity)
        assert any(x["tool"] == "get_task_context" and x["status"] == "ok" for x in activity)
        assert any(x["tool"] == "get_action_policy_advisory" and x["status"] == "ok" for x in activity)
    finally:
        _stop(api)
