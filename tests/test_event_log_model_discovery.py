"""Automatic process model discovery into a reviewable catalog draft (roadmap #326 G4)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import event_log_model_discovery as discovery
from server import event_log_workspace as workspace
from shared.process_model import ProcessModel

ROOT = Path(__file__).resolve().parents[1]
START = datetime(2024, 1, 1, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _log(traces):
    rows = ["case,activity,time"]
    for case, trace in enumerate(traces):
        rows += [f"c{case},{a},{(START + timedelta(days=case, hours=h)).isoformat()}" for h, a in enumerate(trace)]
    return workspace.import_log(BytesIO(("\n".join(rows) + "\n").encode()), name="discovery", source_format="csv",
                                mapping={"case_column": "case", "activity_column": "activity",
                                         "timestamp_column": "time"})["log_id"]


def _shape(node):
    if node["op"] == "activity":
        return node["activity"]
    if node["op"] == "tau":
        return "tau"
    return node["op"] + "(" + ", ".join(_shape(c) for c in node["children"]) + ")"


def _kinds(model):
    return sorted(a["kind"] for a in model["activities"])


def _reviewed(model):
    return ProcessModel.model_validate({**model, "goal": "Handle incoming requests"})


def test_choice_skip_loop_and_parallel_become_catalog_structures():
    choice = discovery.discover(_log([["A", "B", "D"]] * 12 + [["A", "C", "D"]] * 8))
    assert _shape(choice["tree"]) == "seq(A, xor(B, C), D)"
    assert _kinds(choice["process_model"]).count("decision") == 1
    rules = [r["text"] for r in choice["process_model"]["business_rules"]]
    assert any("12 of 20 cases (60%)" in text for text in rules)
    assert all(not r["user_confirmed"] for r in choice["process_model"]["business_rules"])
    _reviewed(choice["process_model"])

    skip = discovery.discover(_log([["A", "B", "C"]] * 12 + [["A", "C"]] * 8))
    assert _shape(skip["tree"]) == "seq(A, xor(tau, B), C)"
    assert any("skipped this step" in r["text"] for r in skip["process_model"]["business_rules"])

    loop = discovery.discover(_log([["A", "B", "D"]] * 10 + [["A", "B", "C", "B", "D"]] * 6
                                   + [["A", "B", "C", "B", "C", "B", "D"]] * 4))
    assert _shape(loop["tree"]) == "seq(A, loop(B, C), D)"
    assert loop["tree"]["children"][1]["repeats"] == 14
    edges = {(t["source"], t["target"]) for t in loop["process_model"]["transitions"]}
    assert ("act_3", "loop_1") in edges   # the redo step goes back round

    parallel = discovery.discover(_log([["A", "B", "C", "D"]] * 10 + [["A", "C", "B", "D"]] * 10))
    assert _shape(parallel["tree"]) == "seq(A, and(B, C), D)"
    assert {"parallel_split", "parallel_join"} <= set(_kinds(parallel["process_model"]))
    model = _reviewed(parallel["process_model"])
    assert model.linear_path() is None
    for result in (choice, skip, loop, parallel):
        assert result["quality"]["fitting_case_share"] == 1
        assert result["process_model"]["goal"] == ""   # the reviewer states the goal
        assert result["process_model"]["execution_authorized"] is False


def test_noise_filter_drops_rare_deviations_and_reports_fitness():
    log = _log([["A", "B", "C"]] * 50 + [["A", "C", "B"]])
    filtered = discovery.discover(log)
    assert _shape(filtered["tree"]) == "seq(A, B, C)"
    assert filtered["quality"]["fitting_cases"] == 50 and filtered["quality"]["checked_cases"] == 51
    exact = discovery.discover(log, noise=0)
    assert _shape(exact["tree"]) == "seq(A, and(B, C))"
    assert exact["quality"]["fitting_case_share"] == 1


def test_unrepresentable_parallel_choice_is_ordered_when_it_fits_else_any_order():
    tree = discovery._parallel(
        [{"op": "xor", "children": [discovery._leaf("B"), discovery._leaf("D")], "cases": [11, 9]},
         discovery._leaf("C")], [{"B", "D"}, {"C"}],
        [(("B", "C"), 9), (("D", "C"), 8), (("C", "B"), 2), (("C", "D"), 1)], 0.15, notes := [], 0)
    assert _shape(tree) == "seq(xor(B, D), C)" and "most common observed order" in notes[0]
    result = discovery.discover(_log([["A", "B", "C", "E"]] * 5 + [["A", "C", "B", "E"]] * 5
                                     + [["A", "D", "C", "E"]] * 5 + [["A", "C", "D", "E"]] * 5))
    assert _shape(result["tree"]) == "seq(A, loop(xor(B, C, D), tau), E)"
    assert len(result["simplifications"]) == 1 and "no single order fits" in result["simplifications"][0]
    assert result["quality"]["fitting_case_share"] == 1
    _reviewed(result["process_model"])


def test_activity_share_labels_cohorts_size_bound_and_bad_settings(monkeypatch):
    log = _log([["Receive #1", "Check", "Close"]] * 18 + [["Receive #1", "Rare step", "Close"]] * 2)
    reduced = discovery.discover(log, activities=0.9)
    assert reduced["quality"]["activities_hidden"] == 1
    assert [a["label"] for a in reduced["process_model"]["activities"] if a["kind"] == "activity"] == [
        "Receive 1", "Check", "Close"]
    assert reduced["process_model"]["baseline_path"] == ["Receive #1", "Check", "Close"]
    rare = discovery.discover(log, cohort={"activity": "Rare step"})
    assert _shape(rare["tree"]) == "seq(Receive #1, Rare step, Close)"
    for bad in ({"noise": 1}, {"noise": -0.1}, {"activities": 0}):
        with pytest.raises(workspace.EventLogWorkspaceError):
            discovery.discover(log, **bad)
    wide = _log([[f"Step {i}" for i in range(45)]] * 3)
    with pytest.raises(workspace.EventLogWorkspaceError, match="Lower the activity share"):
        discovery.discover(wide)
    branchy = _log([["A", f"B{i}", "C"] for i in range(36)])   # 38 activities plus start, end and gateways
    with pytest.raises(workspace.EventLogWorkspaceError, match="allows 40"):
        discovery.discover(branchy)


def test_unstructured_behaviour_is_a_reported_flower_loop():
    result = discovery.discover(_log([["A", "C", "B", "A"], ["B", "C", "A"], ["C", "A", "B", "C"]] * 5))
    assert result["tree"]["op"] == "loop" and result["tree"].get("unstructured") is True
    assert len(result["simplifications"]) == 1 and "any order" in result["simplifications"][0]
    rules = [r["text"] for r in result["process_model"]["business_rules"]]
    assert any(" events (" in text for text in rules)   # flower shares count events, not cases
    assert result["quality"]["fitting_case_share"] == 1
    _reviewed(result["process_model"])


def test_real_http_route_saves_the_reviewed_draft_and_round_trips_bpmn(tmp_path):
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
rows = ["case,activity,time"]
for c in range(20):
    path = ["Receive", "Approve" if c < 12 else "Reject", "Close"]
    rows += [f"{c},{a},2024-01-{c + 1:02d}T{h:02d}:00:00Z" for h, a in enumerate(path)]
doc = ("\n".join(rows) + "\n").encode()
with TestClient(app) as client:
    headers = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    made = client.post("/v1/event-logs/import?format=csv&name=x&case_column=case&activity_column=activity"
                       "&timestamp_column=time", content=doc, headers={**headers, "x-owg-local-storage-confirmed": "true"})
    path = "/v1/event-logs/" + made.json()["log_id"] + "/model-discovery"
    assert client.get(path, headers={"Authorization": "Bearer " + local_auth.ensure_api_token()}).status_code in (401, 403)
    assert client.get(path + "?noise=1", headers=headers).status_code == 422
    found = client.get(path + "?noise=0.2", headers=headers)
    assert found.status_code == 200 and found.headers["cache-control"] == "no-store", found.text
    model = found.json()["process_model"]
    assert client.post("/v1/process-catalog", json={"title": "Draft", "user_confirmed": True, "process_model": model},
                       headers=headers).status_code == 422   # the goal must be stated first
    model["goal"] = "Decide incoming requests"
    saved = client.post("/v1/process-catalog", json={"title": "Discovered", "user_confirmed": True,
                                                    "process_model": model}, headers=headers)
    assert saved.status_code == 200, saved.text
    record = saved.json()
    bpmn = client.get("/v1/process-catalog/export", params={"workflow_id": record["workflow_id"],
                      "revision": record["revision"], "format": "bpmn"}, headers=headers)
    assert bpmn.status_code == 200 and b"exclusiveGateway" in bpmn.content, bpmn.text
    back = client.post("/v1/process-catalog/import-bpmn", content=bpmn.content,
                       headers={**headers, "content-type": "application/xml"})
    assert back.status_code == 200, back.text
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"


@pytest.mark.skipif(not os.environ.get("OWG_SCALE_ACCEPTANCE"), reason="scale acceptance runs on demand")
def test_one_million_events_discover_within_budget():
    import time
    rows = ["case,activity,time"]
    for c in range(200_000):
        middle = ["Check", "Fix", "Check"] if c % 5 == 0 else ["Check"]
        branch = "Approve" if c % 3 else "Reject"
        path = ["Receive", *middle, branch, "Close"]
        rows += [f"c{c},{a},{(START + timedelta(seconds=c * 10 + s)).isoformat()}" for s, a in enumerate(path)]
    log = workspace.import_log(BytesIO(("\n".join(rows) + "\n").encode()), name="big", source_format="csv", mapping={
        "case_column": "case", "activity_column": "activity", "timestamp_column": "time"})["log_id"]
    started = time.perf_counter()
    result = discovery.discover(log)
    assert time.perf_counter() - started < 30 and result["quality"]["fitting_case_share"] > 0.9
