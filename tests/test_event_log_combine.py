"""Combining saved logs into a new log: shared or separate cases, prefixed events, sources unchanged."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from io import BytesIO

import pytest

from server import event_log_combine as combine
from server import event_log_workspace as workspace
from server.event_log_workspace import EventLogWorkspaceError

MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time"}


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()


def _log(name, rows, mapping=MAPPING):
    doc = "case,activity,time\n" + "\n".join(rows)
    return workspace.import_log(BytesIO(doc.encode()), name=name, source_format="csv", mapping=mapping)["log_id"]


def _orders():
    return _log("Orders", [f"c{i},Order,2024-01-0{1 + i}T08:00:00Z" for i in range(4)])


def _invoices():
    return _log("Invoices", [f"c{i},Invoice,2024-01-0{1 + i}T12:00:00Z" for i in range(2, 6)])


def test_shared_cases_join_the_systems_of_one_process():
    a, b = _orders(), _invoices()
    r = combine.combine({"name": "Order to invoice", "log_ids": [a, b]})
    new = r["log"]["log_id"]
    assert r["copied"] == {"events": 8, "objects": 6} and r["ids_in_several_sources"] == 2
    assert [s["event_id_prefix"] for s in r["combined_from"]] == ["s1:", "s2:"] and r["sources_unchanged"]
    assert workspace.summary(new)["cases"] == 6
    variants = {tuple(v["path"]): v["cases"]
                for v in workspace.variants(new)["variants"]}
    assert variants[("Order", "Invoice")] == 2 and variants[("Order",)] == 2 and variants[("Invoice",)] == 2
    # The sources are untouched and every event names its source log.
    assert workspace.summary(a)["events"] == 4 and workspace.summary(b)["events"] == 4
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            rows = conn.execute("SELECT event_id, attributes FROM events WHERE log_id = ? ORDER BY event_id", [new]).fetchall()
        finally:
            conn.close()
    assert all(e.startswith(("s1:", "s2:")) for e, _ in rows)
    assert {json.loads(attrs)["source_log"] for _, attrs in rows} == {"Orders", "Invoices"}


def test_separate_cases_never_merge_equal_ids():
    a = _log("North", [f"c{i},A,2024-02-0{1 + i}T08:00:00Z" for i in range(3)])
    b = _log("South", [f"c{i},A,2024-02-0{1 + i}T09:00:00Z" for i in range(3)])
    r = combine.combine({"name": "Both regions", "log_ids": [a, b], "cases": "separate"})
    assert r["copied"] == {"events": 6, "objects": 6} and workspace.summary(r["log"]["log_id"])["cases"] == 6
    shared = combine.combine({"name": "Merged ids", "log_ids": [a, b]})
    assert workspace.summary(shared["log"]["log_id"])["cases"] == 3


def test_validation_and_limits(monkeypatch):
    a, b = _orders(), _invoices()
    for bad in ({"name": "x", "log_ids": [a]}, {"name": "x", "log_ids": [a, a]}, {"name": " ", "log_ids": [a, b]},
                {"name": "x", "log_ids": [a, b], "cases": "maybe"}, {"name": "x", "log_ids": [a, b], "extra": 1},
                {"name": "x", "log_ids": [a, "log_0000000000000000"]}, {"name": "<b>", "log_ids": [a, b]}, []):
        with pytest.raises(EventLogWorkspaceError):
            combine.combine(bad)
    monkeypatch.setattr(workspace, "MAX_LOGS", 2)
    with pytest.raises(EventLogWorkspaceError, match="delete an event log"):
        combine.combine({"name": "x", "log_ids": [a, b]})
    assert len(workspace.list_logs()["logs"]) == 2


def test_an_existing_source_log_attribute_is_replaced_and_counted():
    rows = ["case,activity,time,source_log"] + [f"c{i},A,2024-03-0{1 + i}T08:00:00Z,old" for i in range(3)]
    a = workspace.import_log(BytesIO("\n".join(rows).encode()), name="Tagged", source_format="csv",
                             mapping={**MAPPING, "attribute_columns": ["source_log"]})["log_id"]
    r = combine.combine({"name": "Two", "log_ids": [a, _orders()]})
    assert r["source_attribute_replaced_on_events"] == 3
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            names = {json.loads(x)["source_log"] for (x,) in conn.execute(
                "SELECT attributes FROM events WHERE log_id = ?", [r["log"]["log_id"]]).fetchall()}
        finally:
            conn.close()
    assert names == {"Tagged", "Orders"}


def test_deleting_the_combined_log_keeps_the_sources():
    a, b = _orders(), _invoices()
    new = combine.combine({"name": "Both", "log_ids": [a, b]})["log"]["log_id"]
    workspace.delete_log(new)
    assert {l["log_id"] for l in workspace.list_logs()["logs"]} == {a, b}


def test_route_is_owner_only(tmp_path):
    code = r'''
from io import BytesIO
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth, event_log_workspace as workspace
m={"case_column":"case","activity_column":"activity","timestamp_column":"time"}
ids=[workspace.import_log(BytesIO(("case,activity,time\n"+"\n".join(f"c{i},{a},2024-03-0{1+i}T00:00:00Z" for i in range(3))).encode()),
     name=a, source_format="csv", mapping=m)["log_id"] for a in ("A","B")]
with TestClient(app) as client:
    owner={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
    bearer={'Authorization':'Bearer '+local_auth.ensure_api_token()}
    body={"name":"AB","log_ids":ids}
    assert client.post('/v1/event-logs/combine',json=body,headers=bearer).status_code in (401,403)
    assert client.post('/v1/event-logs/combine',json=body).status_code in (401,403)
    r=client.post('/v1/event-logs/combine',json=body,headers=owner)
    assert r.status_code==200 and r.headers['cache-control']=='no-store', r.text
    assert r.json()['copied']['events']==6
    assert client.post('/v1/event-logs/combine',json={"name":"x","log_ids":ids[:1]},headers=owner).status_code==422
'''
    env = {**os.environ, 'WORKFLOW_OBSERVER_DATA': str(tmp_path / 'data'), 'WORKFLOW_OBSERVER_AUTH_DIR': str(tmp_path / 'auth')}
    subprocess.run([sys.executable, '-c', code], env=env, check=True, timeout=60)
