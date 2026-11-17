"""Owner-chosen, per-log, read-only process intelligence for connected AI agents (MCP)."""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from io import BytesIO
from pathlib import Path

import pytest

from server import event_log_agent_sharing as sharing
from server import event_log_workspace as workspace
from server import process_intelligence_settings as settings

ROOT = Path(__file__).resolve().parents[1]
MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time", "resource_column": "who"}


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()
    monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR", str(tmp_path / "auth"))
    (tmp_path / "auth").mkdir()
    monkeypatch.delenv("OWG_PROCESS_INTELLIGENCE_TOOLS", raising=False)


def _log(name="orders"):
    rows = ["case,activity,time,who"]
    for i in range(12):
        slow = i % 2 == 0
        rows += [f"c{i},Receive,2024-03-{1 + i:02d}T08:00:00Z,clerk",
                 f"c{i},Approve,2024-03-{1 + i:02d}T09:00:00Z,{'bob' if slow else 'ana'}",
                 f"c{i},Pay,2024-03-{(4 if slow else 1) + i:02d}T10:00:00Z,bot"]
    return workspace.import_log(BytesIO("\n".join(rows).encode()), name=name, source_format="csv", mapping=MAPPING)["log_id"]


def test_sharing_levels_and_revocation():
    log, other = _log(), _log("other")
    assert sharing.share_status(log)["level"] is None and settings.tools_enabled() is False
    with pytest.raises(sharing.NotShared):
        sharing.overview(log)
    assert sharing.shared_logs()["logs"] == []
    sharing.set_share(log, "aggregates")
    assert settings.tools_enabled() is True
    listed = sharing.shared_logs()["logs"]
    assert [(entry["log_id"], entry["level"], entry["case_notions"]) for entry in listed] == [(log, "aggregates", ["case"])]
    ov = sharing.overview(log, sla_hours=24)
    assert ov["cases"] == 12 and ov["top_variants"][0]["path"] == ["Receive", "Approve", "Pay"]
    assert "throughput_seconds" in ov["kpis"] and ov["kpis"]["sla"]["breached_cases"] == 6
    rc = sharing.root_cause(log, "duration_over", hours=24)
    bob = next(a for a in rc["associations"] if a["feature"] == {"kind": "resource", "value": "bob"})
    assert bob["risk_difference"] == 1.0 and bob["holds_after_bonferroni"]
    # Case-level reading needs the cases level; the other log stays closed.
    with pytest.raises(sharing.NotShared):
        sharing.explain_case(log, "c0")
    with pytest.raises(sharing.NotShared):
        sharing.overview(other)
    sharing.set_share(log, "cases")
    assert sharing.explain_case(log, "c0")["case_id"] == "c0"
    assert sharing.slowest_cases(log, limit=3)["cases"][0]["case_id"] in {"c0", "c2", "c4", "c6", "c8", "c10"}
    with pytest.raises(workspace.EventLogWorkspaceError):
        sharing.set_share(log, "everything")
    sharing.set_share(log, None)
    with pytest.raises(sharing.NotShared):
        sharing.overview(log)
    assert settings.tools_enabled() is False


def test_deleting_the_log_removes_its_share():
    log = _log()
    sharing.set_share(log, "cases")
    workspace.delete_log(log)
    assert sharing.shared_logs()["logs"] == []
    with pytest.raises(sharing.NotShared):
        sharing.overview(log)


def test_routes_owner_shares_agent_reads(tmp_path):
    code = r'''
import json
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
from server.ai_access import set_ai_access
rows = ["case,activity,time"] + [f"c{i},A,2024-03-0{1+i}T08:00:00Z" for i in range(6)] + [f"c{i},B,2024-03-0{1+i}T09:00:00Z" for i in range(6)]
with TestClient(app) as client:
    owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    agent = {"Authorization": "Bearer " + local_auth.ensure_api_token(), "X-OpenWorkGraph-Context": "ai"}
    log_id = client.post("/v1/event-logs/import?format=csv&name=orders&case_column=case&activity_column=activity&timestamp_column=time",
                         content="\n".join(rows).encode(), headers={**owner, "X-OWG-Local-Storage-Confirmed": "true"}).json()["log_id"]
    set_ai_access(True)
    # Not shared: indistinguishable from an unknown log.
    assert client.get("/v1/process-intelligence/logs", headers=agent).json()["logs"] == []
    unknown = client.get("/v1/process-intelligence/log_" + "0" * 16 + "/overview", headers=agent)
    unshared = client.get(f"/v1/process-intelligence/{log_id}/overview", headers=agent)
    assert unknown.status_code == unshared.status_code == 404 and unknown.json() == unshared.json()
    # Only the dashboard can share; an agent cannot widen its own access.
    assert client.post(f"/v1/event-logs/{log_id}/agent-share", json={"level": "cases"}, headers=agent).status_code == 403
    r = client.post(f"/v1/event-logs/{log_id}/agent-share", json={"level": "aggregates"}, headers=owner)
    assert r.status_code == 200 and r.json()["level"] == "aggregates", r.text
    assert client.post(f"/v1/event-logs/{log_id}/agent-share", json={"level": "all"}, headers=owner).status_code == 422
    r = client.get(f"/v1/process-intelligence/{log_id}/overview", headers=agent)
    assert r.status_code == 200 and r.json()["cases"] == 6, r.text
    assert client.get(f"/v1/process-intelligence/{log_id}/case?case_id=c0", headers=agent).status_code == 404
    r = client.get(f"/v1/process-intelligence/{log_id}/root-cause?outcome=rework", headers=agent)
    assert r.status_code == 200, r.text
    # The global AI-access switch still applies.
    set_ai_access(False)
    assert client.get(f"/v1/process-intelligence/{log_id}/overview", headers=agent).status_code == 403
    set_ai_access(True)
    client.post(f"/v1/event-logs/{log_id}/agent-share", json={"level": "cases"}, headers=owner)
    r = client.get(f"/v1/process-intelligence/{log_id}/case?case_id=c0", headers=agent)
    # At the default AI-context detail level identifiers reach the agent pseudonymised.
    assert r.status_code == 200 and r.json()["events"] == 2 and r.json()["case_id"] != "c0", r.text
    assert r.headers["X-OpenWorkGraph-Detail-Level"] != "full"
    # Agents still cannot read the dashboard-only event-log API.
    assert client.get(f"/v1/event-logs/{log_id}/summary", headers=agent).status_code == 403
    client.post(f"/v1/event-logs/{log_id}/agent-share", json={"level": None}, headers=owner)
    assert client.get(f"/v1/process-intelligence/{log_id}/overview", headers=agent).status_code == 404
print("ok")
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "srv"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "srvauth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    done = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)
    assert done.returncode == 0 and done.stdout.strip().endswith("ok"), f"stdout={done.stdout}\nstderr={done.stderr}"


async def _listed_tools(tmp_path: Path, enabled: bool) -> set[str]:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    env = os.environ.copy()
    env.update({"PYTHONPATH": str(ROOT), "WORKFLOW_OBSERVER_DATA": str(tmp_path / "mcpdata"),
                "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "mcpauth"), "WORKFLOW_OBSERVER_API": "http://127.0.0.1:1"})
    for key in ("OWG_EXPERIMENTAL_GOVERNANCE", "OWG_OPEN_WORK_EXPERIMENTAL_TOOLS"):
        env.pop(key, None)
    env["OWG_PROCESS_INTELLIGENCE_TOOLS"] = "1" if enabled else "0"
    params = StdioServerParameters(command=sys.executable, args=["-m", "mcp_server.compact_stdio"], cwd=str(ROOT), env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return {tool.name for tool in (await session.list_tools()).tools}


PROCESS_TOOLS = {"list_process_logs", "get_process_overview", "get_process_root_cause",
                 "explain_process_case", "get_slowest_process_cases"}


def test_mcp_tools_are_listed_only_when_a_log_is_shared(tmp_path):
    from tests.test_compact_mcp_v087 import DEFAULT_TOOLS
    off = asyncio.run(_listed_tools(tmp_path, False))
    assert off == DEFAULT_TOOLS
    on = asyncio.run(_listed_tools(tmp_path, True))
    assert on == DEFAULT_TOOLS | PROCESS_TOOLS
