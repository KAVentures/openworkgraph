"""Agent mining: observed AI agent runs become an event log that every analysis can use."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from server import event_log_agent_mining as mining
from server import event_log_workspace as workspace
from shared.agent_evidence import agent_event_to_evidence

ROOT = Path(__file__).resolve().parents[1]
BASE = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)


def _run(index: int, *, fail: bool) -> list[dict]:
    common = {"agent_name": "Coder" if index % 2 else "Reviewer", "provider": "test", "framework": "custom-agent",
              "observation_level": "native_trace", "run_id": f"raw-run-secret-{index}"}
    steps = [("run_started", "running", {}),
             ("model_call", "success", {"model": "model-a", "usage": {"input_tokens": 100, "output_tokens": 20}}),
             ("tool_call", "success", {"tool_name": "Grep", "tool_category": "search",
                                       "tool_detail": "SECRET-ARGUMENT"}),
             ("tool_call", "error" if fail else "success", {"tool_name": "Bash", "tool_category": "code"}),
             ("run_finished", "error" if fail else "success", {})]
    start = BASE + timedelta(hours=index)
    gap = 600 if fail else 60
    return [agent_event_to_evidence({**common, "event_id": f"event-secret-{index}-{n}",
                                     "observed_at": (start + timedelta(seconds=n * gap)).isoformat(),
                                     "operation": op, "status": status, **extra})
            for n, (op, status, extra) in enumerate(steps)]


@pytest.fixture(autouse=True)
def _data(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()
    raw = [event for i in range(12) for event in _run(i, fail=i % 4 == 0)]  # only Reviewer runs fail
    from server import procedural_memory
    calls = []

    def fake(**kwargs):
        calls.append(kwargs)
        return raw
    monkeypatch.setattr(procedural_memory, "load_recent_evidence", fake)
    return calls


def test_runs_become_cases_with_steps_and_attributes(_data):
    result = mining.import_agent_runs({"name": "agents"})
    assert (result["runs"], result["events"], result["model_calls_included"]) == (12, 48, False)
    assert _data[0]["agent_only"] is True
    log = result["log_id"]
    summary = workspace.summary(log)
    assert (summary["cases"], summary["events"]) == (12, 48)
    variants = workspace.variants(log)["variants"]
    paths = {tuple(v["activities"] if "activities" in v else v["path"]) for v in variants}
    assert ("Run started", "Tool: Grep", "Tool: Bash (error)", "Run failed") in paths
    assert ("Run started", "Tool: Grep", "Tool: Bash", "Run finished") in paths
    # Nothing but structure crosses over: no native IDs, tool arguments or event IDs.
    dumped = json.dumps([summary, variants, workspace.list_logs()])
    for secret in ("raw-run-secret", "SECRET-ARGUMENT", "event-secret"):
        assert secret not in dumped
    # Ordinary analyses apply: failed runs are associated with the Reviewer agent.
    from server import event_log_analytics as analytics
    rc = analytics.root_cause(log, "contains_activity", activity="Run failed")
    reviewer = next(a for a in rc["associations"] if a["feature"] == {"kind": "attribute", "value": "Agent=Reviewer"})
    assert reviewer["risk_difference"] == 0.5


def test_model_calls_and_case_attributes(_data):
    result = mining.import_agent_runs({"name": "agents", "include_model_calls": True, "max_runs": 6})
    assert (result["runs"], result["events"]) == (6, 30)
    from server import event_log_case_attribute_values as values
    found = {a["name"]: a for a in values.values(result["log_id"])["attributes"]}
    assert set(found) == set(mining.CASE_ATTRIBUTES)
    assert found["Input tokens"]["numeric"] and found["Input tokens"]["max"] == 100
    assert {v["value"] for v in found["Outcome"]["top_values"]} == {"success", "error"}


def test_validation(_data):
    for bad in ({"max_runs": 0}, {"max_runs": "5"}, {"include_model_calls": "yes"}, {"since": "2026-09-01"},
                {"unexpected": 1}, []):
        with pytest.raises(workspace.EventLogWorkspaceError):
            mining.import_agent_runs(bad)


def test_no_runs_is_a_clear_error(monkeypatch):
    from server import procedural_memory
    monkeypatch.setattr(procedural_memory, "load_recent_evidence", lambda **kwargs: [])
    with pytest.raises(workspace.EventLogWorkspaceError, match="no observed AI agent runs"):
        mining.import_agent_runs({})


def test_route_is_dashboard_only_and_needs_storage_consent(tmp_path):
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
from server.agent_ingest import ingest_agent_payloads
with TestClient(app) as client:
    owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    path = "/v1/event-logs/import-agent-runs"
    consent = {"X-OWG-Local-Storage-Confirmed": "true"}
    assert client.post(path, json={}, headers={**bearer, **consent}).status_code == 403
    assert client.post(path, json={}, headers=owner).status_code == 403
    r = client.post(path, json={}, headers={**owner, **consent})
    assert r.status_code == 422 and "no observed AI agent runs" in r.text, r.text
    ingest_agent_payloads([{"agent_name": "Coder", "provider": "test", "framework": "custom-agent",
                            "observation_level": "native_trace", "run_id": "r1", "session_id": "s1",
                            "event_id": f"e{n}", "operation": op, "status": "success",
                            "observed_at": f"2026-09-01T08:0{n}:00+00:00"}
                           for n, op in enumerate(["run_started", "run_finished"])])
    r = client.post(path, json={"name": "agents", "max_runs": 10}, headers={**owner, **consent})
    assert r.status_code == 200 and (r.json()["runs"], r.json()["events"]) == (1, 2), r.text
    assert client.post(path, json={"max_runs": 0}, headers={**owner, **consent}).status_code == 422
print("ok")
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "srv"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "srvauth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    done = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)
    assert done.returncode == 0 and done.stdout.strip().endswith("ok"), f"stdout={done.stdout}\nstderr={done.stderr}"
