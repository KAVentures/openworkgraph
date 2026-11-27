"""JSON Lines export: one object per event, read back by the JSON Lines import to the CSV export."""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from io import BytesIO

import pytest

from server import event_log_jsonl as jsonl
from server import event_log_workspace as workspace
from server.event_log_cohort_export import export_matching_cases

MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time", "resource_column": "who"}


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()


def _log():
    rows = ["case,activity,time,who", '007,"Prüfen ""A"", 1",2024-01-01T09:00:00+01:00,"Ann, <Lead>"',
            "007,Pay,2024-01-01T09:00:00Z,", "c2,Pay,2024-01-02T09:00:00Z,bob", "c3,=SUM(1),2024-01-03T09:00:00Z,"]
    return workspace.import_log(BytesIO("\n".join(rows).encode()), name="x", source_format="csv", mapping=MAPPING)["log_id"]


def _export(log, fmt, case_type=None):
    out = io.StringIO()
    workspace.export_log(log, fmt, out, case_type)
    return out.getvalue()


def test_each_line_is_one_event_with_the_csv_fields():
    lines = _export(_log(), "jsonl").splitlines()
    events = [json.loads(line) for line in lines]
    assert events[0] == {"case_id": "007", "activity": 'Prüfen "A", 1', "timestamp": "2024-01-01T08:00:00Z",
                         "resource": "Ann, <Lead>"}
    assert events[1]["resource"] is None and events[3]["activity"] == "=SUM(1)"      # not neutralised: exact data
    assert [list(e) for e in events] == [["case_id", "activity", "timestamp", "resource"]] * 4
    assert "Prüfen" in lines[0]                                                     # UTF-8, not \\u escapes


def test_jsonl_import_reads_it_back_to_the_csv_export(tmp_path):
    log = _log()
    path = tmp_path / "x.jsonl"
    path.write_text(_export(log, "jsonl"), encoding="utf-8")
    with open(path, "rb") as f:
        back = jsonl.to_csv(f)
    assert back["csv"].replace("\r\n", "\n") == _export(log, "csv").replace("\r\n", "\n")


def test_matching_cases_jsonl_matches_the_matching_csv_rows():
    log = _log()
    spec = {"cohort": {"activity": "Pay"}}
    out = io.StringIO()
    export_matching_cases(log, spec, out, export_format="jsonl")
    events = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [(e["case_id"], e["activity"]) for e in events] == [("007", 'Prüfen "A", 1'), ("007", "Pay"), ("c2", "Pay")]
    csv_out = io.StringIO()
    export_matching_cases(log, spec, csv_out, export_format="csv")
    assert len(csv_out.getvalue().splitlines()) == len(events) + 1


def test_object_centric_log_exports_per_case_notion():
    ocel = {"objectTypes": [{"name": "order", "attributes": []}, {"name": "item", "attributes": []}],
            "eventTypes": [{"name": "Open", "attributes": []}, {"name": "Pick", "attributes": []}],
            "objects": [{"id": "o1", "type": "order"}, {"id": "i1", "type": "item"}, {"id": "i2", "type": "item"}],
            "events": [{"id": "e1", "type": "Open", "time": "2024-01-01T00:00:00Z",
                        "relationships": [{"objectId": "o1", "qualifier": ""}, {"objectId": "i1", "qualifier": ""},
                                          {"objectId": "i2", "qualifier": ""}]},
                       {"id": "e2", "type": "Pick", "time": "2024-01-02T00:00:00Z",
                        "relationships": [{"objectId": "i1", "qualifier": ""}]}]}
    log = workspace.import_log(BytesIO(json.dumps(ocel).encode()), name="o", source_format="ocel")["log_id"]
    items = [json.loads(line) for line in _export(log, "jsonl", "item").splitlines()]
    orders = [json.loads(line) for line in _export(log, "jsonl", "order").splitlines()]
    assert [(e["case_id"], e["activity"]) for e in items] == [("i1", "Open"), ("i1", "Pick"), ("i2", "Open")]
    assert [(e["case_id"], e["activity"]) for e in orders] == [("o1", "Open")]


def test_route_is_owner_only(tmp_path):
    code = r'''
import json
from io import BytesIO
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth, event_log_workspace as workspace
rows = ["case,activity,time"] + [f"c{i},{'AB'[i % 2]},2024-03-0{1+i}T00:00:00Z" for i in range(3)]
log = workspace.import_log(BytesIO("\n".join(rows).encode()), name="q", source_format="csv",
    mapping={"case_column":"case","activity_column":"activity","timestamp_column":"time"})["log_id"]
with TestClient(app) as client:
    owner={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
    bearer={'Authorization':'Bearer '+local_auth.ensure_api_token()}
    url='/v1/event-logs/'+log+'/export?format=jsonl'
    assert client.get(url,headers=bearer).status_code in (401,403)
    r=client.get(url,headers=owner)
    assert r.status_code==200 and r.headers['content-type'].startswith('application/x-ndjson'), r.text
    assert r.headers['content-disposition'].endswith('.jsonl"') or '.jsonl' in r.headers['content-disposition']
    assert [json.loads(l)['case_id'] for l in r.text.splitlines()]==['c0','c1','c2']
    m=client.post('/v1/event-logs/'+log+'/export/matching-cases?format=jsonl',json={'cohort':{'activity':'A'}},headers=owner)
    assert m.status_code==200 and 'matching-cases.jsonl' in m.headers['content-disposition'], m.text
    assert [json.loads(l)['case_id'] for l in m.text.splitlines()]==['c0','c2']
'''
    env = {**os.environ, 'WORKFLOW_OBSERVER_DATA': str(tmp_path / 'data'), 'WORKFLOW_OBSERVER_AUTH_DIR': str(tmp_path / 'auth')}
    subprocess.run([sys.executable, '-c', code], env=env, check=True, timeout=60)
