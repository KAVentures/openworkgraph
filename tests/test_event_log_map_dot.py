"""Graphviz DOT export of the process map: same map, generated ids, escaped labels."""
from __future__ import annotations

import re
import shutil
import subprocess
from io import BytesIO

import pytest

from server import event_log_map_dot as map_dot
from server import event_log_workspace as workspace

MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time"}


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()


def _log(traces) -> str:
    rows = ["case,activity,time"]
    for i, trace in enumerate(traces):
        rows += [f'c{i},"{a.replace(chr(34), chr(34) * 2)}",2024-03-01T{8 + k:02d}:00:00Z' for k, a in enumerate(trace)]
    return workspace.import_log(BytesIO("\n".join(rows).encode()), name="orders", source_format="csv",
                                mapping=MAPPING)["log_id"]


def test_map_edges_counts_and_median_gaps():
    log = _log([["Receive", "Check", "Ship"]] * 3 + [["Receive", "Ship"]])
    dot, meta = map_dot.map_dot(log)
    assert dot.startswith('digraph "orders (case) process map" {') and dot.rstrip().endswith("}")
    ids = dict(re.findall(r'  (a\d+) \[label="([^"\\]+)\\n\d+"\];', dot))
    by_name = {v: k for k, v in ids.items()}
    assert set(by_name) == {"Receive", "Check", "Ship"} and meta == {"activities": 3, "transitions": 3}
    assert f'{by_name["Receive"]} -> {by_name["Check"]} [label="3 · 1.0h"' in dot
    assert f'{by_name["Receive"]} -> {by_name["Ship"]} [label="1 · 1.0h"' in dot
    assert f'start -> {by_name["Receive"]} [label="4"' in dot and f'{by_name["Ship"]} -> end [label="4"' in dot


def test_slider_settings_are_applied():
    log = _log([["A", "B", "C"]] * 9 + [["A", "X", "C"]])
    dot, meta = map_dot.map_dot(log, activities=0.9)
    assert '"X\\n' not in dot and meta["activities"] == 3


def test_labels_cannot_inject_dot_syntax():
    evil = 'Approve"]; injected -> x [label="'
    log = _log([["Start", evil, "Done\\path"]] * 2)
    dot, _ = map_dot.map_dot(log)
    assert 'label="Approve\\"]; injected -> x [label=\\"\\n2"]' in dot and "Done\\\\path" in dot
    quoted = r'"(?:[^"\\]|\\.)*"'
    attrs = rf'\[\w+=(?:{quoted}|[\w.]+)(?:, \w+=(?:{quoted}|[\w.]+))*\]'
    for line in dot.splitlines()[1:-1]:   # every statement parses with labels as single quoted strings
        assert re.fullmatch(rf'  (?:\w+(?: -> \w+)? {attrs}|rankdir=TB);', line), line
    if shutil.which("dot"):
        done = subprocess.run(["dot", "-Tsvg"], input=dot, capture_output=True, text=True, timeout=30)
        assert done.returncode == 0 and "injected" not in re.sub(r"<text[^>]*>[^<]*</text>", "", done.stdout)


def test_duration_formatting():
    assert map_dot._duration(None) == "n/a" and map_dot._duration(42) == "42s"
    assert map_dot._duration(90) == "1.5m" and map_dot._duration(2 * 86400) == "2.0d"


def test_route_is_dashboard_only(tmp_path):
    import os
    import sys
    from pathlib import Path
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
rows = ["case,activity,time"] + [f"c{i},A,2024-03-0{1+i}T08:00:00Z" for i in range(6)] + [f"c{i},B,2024-03-0{1+i}T09:00:00Z" for i in range(6)]
with TestClient(app) as client:
    owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    log_id = client.post("/v1/event-logs/import?format=csv&name=t&case_column=case&activity_column=activity&timestamp_column=time",
                         content="\n".join(rows).encode(), headers={**owner, "X-OWG-Local-Storage-Confirmed": "true"}).json()["log_id"]
    path = f"/v1/event-logs/{log_id}/process-map.dot"
    assert client.get(path, headers=bearer).status_code == 403
    r = client.get(path + "?paths=0.5", headers=owner)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/vnd.graphviz"), r.text
    assert r.text.startswith("digraph") and r.headers["x-owg-map"] == "activities=2; transitions=1"
    assert "attachment" in r.headers["content-disposition"]
    assert client.get(path + "?activities=0", headers=owner).status_code == 422
print("ok")
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "srv"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "srvauth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    root = Path(__file__).resolve().parents[1]
    done = subprocess.run([sys.executable, "-c", code], cwd=root, env=env, capture_output=True, text=True, timeout=180)
    assert done.returncode == 0 and done.stdout.strip().endswith("ok"), f"stdout={done.stdout}\nstderr={done.stderr}"
