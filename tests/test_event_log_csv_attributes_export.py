"""CSV export with retained event attributes: one column per attribute, read back by the CSV import."""
from __future__ import annotations

import csv
import io
import json
import os
import subprocess
import sys
from io import BytesIO

import pytest

from server import event_log_workspace as workspace
from server.event_log_cohort_export import export_matching_cases
from server.event_log_workspace import EventLogWorkspaceError

MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time", "resource_column": "who",
           "attribute_columns": ["region", "amount", "note"]}


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()


def _log():
    rows = ["case,activity,time,who,region,amount,note",
            'c1,Receive,2024-01-01T08:00:00Z,ann,north,5,"=1+1, ""quoted"""',
            "c1,Pay,2024-01-01T09:00:00Z,,north,,",
            "c2,Receive,2024-01-02T08:00:00Z,bob,south,7.50,ok"]
    return workspace.import_log(BytesIO("\n".join(rows).encode()), name="x", source_format="csv", mapping=MAPPING)["log_id"]


def _rows(text):
    return list(csv.DictReader(io.StringIO(text)))


def _export(log, fmt="csv-attributes"):
    out = io.StringIO()
    workspace.export_log(log, fmt, out)
    return out.getvalue()


def test_columns_are_the_csv_fields_then_sorted_attributes():
    text = _export(_log())
    assert text.splitlines()[0] == "case_id,activity,timestamp,resource,amount,note,region"
    rows = _rows(text)
    assert rows[0] == {"case_id": "c1", "activity": "Receive", "timestamp": "2024-01-01T08:00:00Z", "resource": "ann",
                       "amount": "5", "note": '=1+1, "quoted"', "region": "north"}
    assert rows[2]["amount"] == "7.50"                      # kept as written, not reformatted
    plain = _rows(_export(_log(), "csv"))
    assert [{k: r[k] for k in plain[0]} for r in rows] == plain   # the four fields equal the exact CSV export


def test_reimport_with_the_attribute_columns_gives_the_same_events():
    log = _log()
    text = _export(log)
    again = workspace.import_log(BytesIO(text.encode()), name="y", source_format="csv",
                                 mapping={"case_column": "case_id", "activity_column": "activity",
                                          "timestamp_column": "timestamp", "resource_column": "resource",
                                          "attribute_columns": ["amount", "note", "region"]})["log_id"]
    assert _export(again) == text


def test_matching_cases_use_the_attributes_of_the_matching_events():
    log = _log()
    out = io.StringIO()
    export_matching_cases(log, {"cohort": {"activity": "Pay"}}, out,
                          export_format="csv-attributes")
    rows = _rows(out.getvalue())
    assert [(r["case_id"], r["activity"], r["region"]) for r in rows] == [("c1", "Receive", "north"), ("c1", "Pay", "north")]
    out = io.StringIO()
    export_matching_cases(log, {"cohort": {}, "events_before": "2024-01-01T08:30:00Z"}, out, export_format="csv-attributes")
    assert [r["activity"] for r in _rows(out.getvalue())] == ["Receive"]


def test_logs_without_attributes_and_non_text_values():
    plain = workspace.import_log(BytesIO(b"case,activity,time\nc1,A,2024-01-01T00:00:00Z\n"), name="p",
                                 source_format="csv",
                                 mapping={"case_column": "case", "activity_column": "activity", "timestamp_column": "time"})
    assert _export(plain["log_id"]).splitlines()[0] == "case_id,activity,timestamp,resource"
    ocel = {"objectTypes": [{"name": "order", "attributes": []}],
            "eventTypes": [{"name": "Open", "attributes": [{"name": "qty", "type": "integer"}]}],
            "objects": [{"id": "o1", "type": "order"}],
            "events": [{"id": "e1", "type": "Open", "time": "2024-01-01T00:00:00Z",
                        "attributes": [{"name": "qty", "value": 3}], "relationships": [{"objectId": "o1", "qualifier": ""}]}]}
    log = workspace.import_log(BytesIO(json.dumps(ocel).encode()), name="o", source_format="ocel")["log_id"]
    assert _rows(_export(log))[0]["qty"] in ("3", "3.0")


def test_attribute_named_like_a_csv_column_is_refused():
    rows = ["case,activity,time,who,resource", "c1,A,2024-01-01T00:00:00Z,ann,desk"]
    log = workspace.import_log(BytesIO("\n".join(rows).encode()), name="r", source_format="csv",
                               mapping={**MAPPING, "attribute_columns": ["resource"]})["log_id"]
    with pytest.raises(EventLogWorkspaceError, match="named 'resource'"):
        _export(log)


def test_routes(tmp_path):
    code = r'''
import csv, io
from io import BytesIO
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth, event_log_workspace as workspace
rows = ["case,activity,time,region", "c1,A,2024-03-01T00:00:00Z,north", "c2,B,2024-03-02T00:00:00Z,south"]
log = workspace.import_log(BytesIO("\n".join(rows).encode()), name="q", source_format="csv",
    mapping={"case_column":"case","activity_column":"activity","timestamp_column":"time","attribute_columns":["region"]})["log_id"]
with TestClient(app) as client:
    owner={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
    bearer={'Authorization':'Bearer '+local_auth.ensure_api_token()}
    url='/v1/event-logs/'+log+'/export?format=csv-attributes'
    assert client.get(url,headers=bearer).status_code in (401,403)
    r=client.get(url,headers=owner)
    assert r.status_code==200 and r.headers['content-type'].startswith('text/csv'), r.text
    assert [row['region'] for row in csv.DictReader(io.StringIO(r.text))]==['north','south']
    m=client.post('/v1/event-logs/'+log+'/export/matching-cases?format=csv-attributes',json={'cohort':{'activity':'B'}},headers=owner)
    assert m.status_code==200 and [row['region'] for row in csv.DictReader(io.StringIO(m.text))]==['south'], m.text
'''
    env = {**os.environ, 'WORKFLOW_OBSERVER_DATA': str(tmp_path / 'data'), 'WORKFLOW_OBSERVER_AUTH_DIR': str(tmp_path / 'auth')}
    subprocess.run([sys.executable, '-c', code], env=env, check=True, timeout=60)
