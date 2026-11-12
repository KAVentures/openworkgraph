"""R24: bounded multi-hop traversal of literal supplied OCEL object relationships."""
from __future__ import annotations

from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import event_log_object_paths as paths
from server import event_log_workspace as workspace

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def local_store(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def fixture():
    return {
        "objectTypes": [{"name": x, "attributes": []}
                        for x in ("order", "item", "invoice", "payment")],
        "eventTypes": [{"name": "Open", "attributes": []}],
        "objects": [
            {"id": "order1", "type": "order", "attributes": [],
             "relationships": [
                 {"objectId": "item1", "qualifier": "contains"},
                 {"objectId": "item2", "qualifier": "contains"},
             ]},
            {"id": "item1", "type": "item", "attributes": [],
             "relationships": [
                 {"objectId": "invoice1", "qualifier": "billed_by"},
                 {"objectId": "order1", "qualifier": "reverse"},
             ]},
            {"id": "item2", "type": "item", "attributes": [],
             "relationships": [{"objectId": "item2", "qualifier": "self"}]},
            {"id": "invoice1", "type": "invoice", "attributes": [],
             "relationships": [{"objectId": "payment1", "qualifier": "settled_by"},
                               {"objectId": "order1", "qualifier": "retro_link"}]},
            {"id": "payment1", "type": "payment", "attributes": [],
             "relationships": []},
            {"id": "isolated", "type": "payment", "attributes": [], "relationships": []},
        ],
        "events": [{"id": "event1", "type": "Open", "time": "2026-01-01T00:00:00Z",
                    "attributes": [], "relationships": [{"objectId": "order1", "qualifier": ""}]}],
    }


def imported():
    return workspace.import_log(BytesIO(json.dumps(fixture()).encode()), name="synthetic",
                                source_format="ocel")["log_id"]


def trace(log, **kwargs):
    return paths.trace(log, {"object_id": "order1", **kwargs})


def test_outgoing_two_hops_and_three_hops_preserve_original_edges_and_types():
    log = imported()
    result = trace(log, direction="outgoing", max_hops=2)
    assert result["format"] == paths.FORMAT
    assert result["retained_assertions_in_log"] == 7
    assert result["selected_object"] == {"object_id": "order1", "object_type": "order"}
    assert result["hops_visited"] == 2 and not result["truncated"]
    assert {x["object_id"]: x["hop"] for x in result["nodes"]} == {
        "order1": 0, "item1": 1, "item2": 1, "invoice1": 2
    }
    assert {(e["source_id"], e["target_id"], e["qualifier"]) for e in result["edges"]} == {
        ("order1", "item1", "contains"), ("order1", "item2", "contains"),
        ("item1", "invoice1", "billed_by"), ("item1", "order1", "reverse"),
        ("item2", "item2", "self")
    }
    assert result["shown_nodes"] == 4 and result["shown_edges"] == 5
    assert "payment1" not in str(result["nodes"])
    assert not result["case_membership_inferred"]
    assert not result["shared_event_correlation_performed"]
    assert not result["source_reads_performed"]
    assert not result["causal_claims"] and not result["execution_authorized"]
    longer = trace(log, direction="outgoing", max_hops=3)
    assert {x["object_id"] for x in longer["nodes"]} == {
        "order1", "item1", "item2", "invoice1", "payment1"
    }
    assert ("invoice1", "payment1", "settled_by") in {
        (e["source_id"], e["target_id"], e["qualifier"]) for e in longer["edges"]
    }


def test_directional_inputs_and_literal_qualifiers_are_not_inferred():
    log = imported()
    inbound = trace(log, direction="incoming", max_hops=1)
    assert {(e["source_id"], e["target_id"], e["qualifier"]) for e in inbound["edges"]} == {
        ("item1", "order1", "reverse"), ("invoice1", "order1", "retro_link")
    }
    both = trace(log, direction="both", max_hops=1)
    assert both["shown_edges"] == 4
    filtered = trace(log, direction="outgoing", max_hops=3, qualifier="contains")
    assert filtered["shown_edges"] == 2
    assert {x["object_id"] for x in filtered["nodes"]} == {"order1", "item1", "item2"}
    absent = trace(log, direction="outgoing", qualifier="", max_hops=3)
    assert absent["shown_edges"] == 0 and absent["shown_nodes"] == 1
    assert absent["selected_object"]["object_id"] == "order1"
    assert paths.trace(log, {"object_id": "isolated"})["shown_nodes"] == 1


def test_node_bound_truncates_without_dangling_edges_or_fabricated_completion():
    log = imported()
    data = trace(log, direction="outgoing", max_hops=3, max_nodes=3)
    assert data["truncated"] and data["truncation_reason"] == "max_nodes_reached"
    ids = {n["object_id"] for n in data["nodes"]}
    assert ids == {"order1", "item1", "item2"}
    assert all(e["source_id"] in ids and e["target_id"] in ids for e in data["edges"])
    assert not data["all_requested_hops_fully_traversed"]


def test_edge_bound_reports_truncation_and_never_drops_existing_edges_from_counts():
    log = imported()
    data = trace(log, direction="outgoing", max_hops=2, max_edges=1)
    assert data["truncated"]
    assert data["truncation_reason"] in {"edge_scan_limit_reached", "max_edges_reached"}
    assert data["shown_edges"] <= 1
    assert data["retained_assertions_in_log"] == 7
    assert not data["all_requested_hops_fully_traversed"]
    assert not data["case_membership_inferred"]


@pytest.mark.parametrize("bad", [
    {"object_id": "missing"},
    {"object_id": ""},
    {"object_id": "order1", "max_hops": 4},
    {"object_id": "order1", "max_hops": "2"},
    {"object_id": "order1", "max_nodes": True},
    {"object_id": "order1", "max_nodes": 1},
    {"object_id": "order1", "max_edges": 201},
    {"object_id": "order1", "direction": "sideways"},
    {"object_id": "order1", "qualifier": "\n"},
    {"object_id": "order1", "unknown_setting": True},
])
def test_invalid_or_unknown_scope_refused_without_guessing(bad):
    log = imported()
    with pytest.raises(workspace.EventLogWorkspaceError):
        paths.trace(log, bad)


def test_real_http_session_guard_and_non_mutating_response(tmp_path):
    script = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
import json
from io import BytesIO
with TestClient(app) as client:
    url = "/v1/event-logs/not_a_log/object-relationships/trace"
    owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    assert client.post(url, json={"object_id":"x"}).status_code in (401,403)
    assert client.post(url, headers=bearer, json={"object_id":"x"}).status_code in (401,403)
    assert client.post(url, headers=owner, json={"object_id":"x"}).status_code == 422
    assert client.get(url, headers=owner).status_code == 405
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"),
                "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    done = subprocess.run([sys.executable, "-c", script], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, f"stdout={done.stdout}\nstderr={done.stderr}"


def test_dashboard_controls_show_bounded_trace_and_exact_source_navigation():
    js = (ROOT / "dashboard/event_log_workspace.js").read_text(encoding="utf-8")
    routes = (ROOT / "server/event_log_routes.py").read_text(encoding="utf-8")
    assert "owgObjectRelationTraceRun" in js
    assert "owgObjectRelationTraceResult" in js
    assert "traceVersion++" in js
    assert "objectButton(e.source_id)" in js
    assert "objectButton(e.target_id)" in js
    assert "'/object-relationships/trace'" in js
    assert '@router.post("/v1/event-logs/{log_id}/object-relationships/trace")' in routes
    assert "_require_dashboard(request)" in routes.split(
        "def event_log_object_relationship_trace(")[1].split("@router.get", 1)[0]
