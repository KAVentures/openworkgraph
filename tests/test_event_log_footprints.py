"""Footprints: log and model ordering relations and where they disagree."""
from __future__ import annotations

from io import BytesIO

import pytest

from server import event_log_footprints as fp
from server import event_log_workspace as workspace
from server.event_log_workspace import EventLogWorkspaceError

MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time"}


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()


def _import(traces):
    rows = ["case,activity,time"]
    for i, trace in enumerate(traces):
        for j, activity in enumerate(trace):
            rows.append(f"c{i},{activity},2024-01-01T{j // 60:02d}:{j % 60:02d}:{i % 60:02d}Z")
    return workspace.import_log(BytesIO("\n".join(rows).encode()), name="t", source_format="csv",
                                mapping=MAPPING)["log_id"]


def _cell(result, a, b):
    row = next(r for r in result["matrix"] if r["activity"] == a)
    return row["cells"][result["activities"].index(b)]


def test_tree_footprint_operators():
    leaf = lambda a: {"op": "activity", "activity": a}  # noqa: E731
    tree = {"op": "seq", "children": [
        leaf("A"),
        {"op": "and", "children": [leaf("B"), leaf("C")]},
        {"op": "xor", "children": [leaf("D"), {"op": "tau"}]},
        {"op": "loop", "children": [leaf("E"), {"op": "tau"}]},
    ]}
    f = fp._tree_fp(tree)
    assert {("A", "B"), ("A", "C"), ("B", "D"), ("C", "E"), ("D", "E"), ("E", "E")} <= set(f.seq)
    assert ("B", "C") in f.par and ("A", "D") not in f.seq
    assert f.start == {"A"} and f.end == {"E"} and not f.skip


def test_fitting_log_agrees_everywhere():
    traces = [["Register", "Check", "Pay", "Close"]] * 10 + [["Register", "Pay", "Check", "Close"]] * 10
    result = fp.footprints(_import(traces), noise=0.0)
    assert result["cases"] == 20 and result["activities"] == ["Check", "Close", "Pay", "Register"]
    assert result["conformance"]["footprint_fitness"] == 1.0 and result["differences_total"] == 0
    assert _cell(result, "Check", "Pay") == {"log": "||", "model": "||"}
    assert _cell(result, "Register", "Check") == {"log": "→", "model": "→"}
    assert _cell(result, "Close", "Register") == {"log": "#", "model": "#"}


def test_noise_filtered_ordering_shows_as_difference():
    traces = [["A", "B", "C"]] * 30 + [["A", "C", "B"]] * 2
    log = _import(traces)
    result = fp.footprints(log, noise=0.2)
    diffs = {(d["from"], d["to"]): d for d in result["differences"]}
    assert ("C", "B") in diffs and diffs[("C", "B")]["kind"] == "in_log_not_in_model"
    assert diffs[("C", "B")]["cases"] == 2
    assert result["conformance"]["footprint_fitness"] < 1
    assert "C" in result["end_activities"]["model"] or "B" in result["end_activities"]["log_only"]
    selection = {"retained_activities": result["activities"], "group": "transition", "activities": ["C", "B"]}
    evidence = fp.footprint_cases(log, selection=selection)
    assert evidence["total"] == diffs[("C", "B")]["cases"] and evidence["occurrences"] == 2
    assert evidence["projected_occurrences"] == 0
    for group in ("start", "end"):
        for activity, count in result[group + "_activities"]["log_only_counts"].items():
            cases = fp.footprint_cases(log, selection={**selection, "group": group, "activities": [activity]})
            assert cases["total"] == count


def test_evidence_counts_adjacency_and_boundaries_in_the_displayed_projection():
    log = _import([["Hidden start", "A", "Middle", "B", "Hidden end"],
                   ["A", "B", "A", "Middle", "B"], ["A", "C", "B"]])
    spec = {"retained_activities": ["A", "B", "C"], "activities": ["A", "B"], "group": "transition", "limit": 1}
    first = fp.footprint_cases(log, selection=spec)
    second = fp.footprint_cases(log, selection={**spec, "after": first["next_after"]})
    assert first["total"] == second["total"] == 2 and first["occurrences"] == 3
    assert first["projected_occurrences"] == 2
    assert first["cases"][0]["occurrences"] == 1 and second["cases"][0]["occurrences"] == 2
    assert second["next_after"] is None
    filtered = fp.footprint_cases(log, selection=spec, cohort={"activity": "Hidden start"})
    assert filtered["total"] == filtered["occurrences"] == filtered["projected_occurrences"] == 1
    for group, activity in (("start", "A"), ("end", "B")):
        evidence = fp.footprint_cases(log, selection={**spec, "activities": [activity], "group": group})
        assert evidence["total"] == evidence["occurrences"] == 3 and evidence["projected_occurrences"] == 1
    for patch in ({"activities": ["A"]}, {"retained_activities": ["C"]}, {"limit": True}, {"after": ""}):
        with pytest.raises(EventLogWorkspaceError):
            fp.footprint_cases(log, selection={**spec, **patch})


def test_bad_input():
    log = _import([["A", "B"]] * 3)
    with pytest.raises(EventLogWorkspaceError):
        fp.footprints(log, noise=1.5)


def test_footprint_evidence_route_requires_dashboard_session(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path

    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
csv = b"case,activity,time\nc,A,2024-03-01T08:00:00Z\nc,B,2024-03-01T09:00:00Z"
with TestClient(app) as client:
    owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    imported = client.post("/v1/event-logs/import?format=csv&name=t&case_column=case&activity_column=activity&timestamp_column=time",
                           content=csv, headers={**owner, "X-OWG-Local-Storage-Confirmed": "true"})
    assert imported.status_code == 200, imported.text
    path = f"/v1/event-logs/{imported.json()['log_id']}/footprints/cases"
    spec = {"retained_activities": ["A", "B"], "group": "transition", "activities": ["A", "B"]}
    assert client.post(path, json=spec, headers=bearer).status_code == 403
    found = client.post(path, json=spec, headers=owner)
    assert found.status_code == 200 and found.headers["cache-control"] == "no-store", found.text
    assert found.json()["cases"][0]["case_id"] == "c"
    assert client.post(path, json={**spec, "group": "bad"}, headers=owner).status_code == 422
'''
    env = {**os.environ, "WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"),
           "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"), "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")}
    result = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
                            env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
