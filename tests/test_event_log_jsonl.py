"""Reading JSON Lines as CSV for the ordinary CSV import."""
from __future__ import annotations

import csv
import io
import json
import os
import subprocess
import sys
from io import BytesIO

import pytest

from server import event_log_jsonl as jl
from server import event_log_workspace as workspace


def _lines(*objs, raw=b""):
    return BytesIO(b"\n".join(json.dumps(o).encode() for o in objs) + raw)


def test_objects_become_rows_with_flattened_columns(tmp_path, monkeypatch):
    src = _lines({"case": "o1", "activity": "Create", "at": "2024-01-01T08:00:00Z", "customer": {"id": 7, "tier": {"name": "gold"}}},
                 {"case": "o1", "activity": "Ship", "at": "2024-01-01T09:00:00Z", "tags": ["a", "b"], "paid": True, "note": None},
                 raw=b"\n\n")
    r = jl.to_csv(src)
    rows = list(csv.reader(io.StringIO(r["csv"])))
    assert rows[0] == ["case", "activity", "at", "customer.id", "customer.tier.name", "tags", "paid", "note"]
    assert rows[1] == ["o1", "Create", "2024-01-01T08:00:00Z", "7", "gold", "", "", ""]
    assert rows[2][5:] == ['["a","b"]', "true", ""]
    assert (r["rows"], r["columns"], r["stored"]) == (2, 8, False)
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)
    log = workspace.import_log(BytesIO(r["csv"].encode()), name="j", source_format="csv",
                               mapping={"case_column": "case", "activity_column": "activity", "timestamp_column": "at"})
    assert log["events"] == 2


def test_bom_depth_and_refusals(monkeypatch):
    assert jl.to_csv(BytesIO(b'\xef\xbb\xbf{"a": 1}\n'))["csv"] == "a\n1\n"
    deep = {"a": {"b": {"c": {"d": {"e": {"f": 1}}}}}}
    assert list(csv.reader(io.StringIO(jl.to_csv(_lines(deep))["csv"])))[0] == ["a.b.c.d.e"]
    for data, match in ((b'{"a":1}\nnot json\n', "line 2 is not valid JSON"), (b"[1,2]\n", "line 1 is not a JSON object"),
                        (b"\n \n", "no JSON objects"), (b'{"a":"\xff"}', "line 1 is not valid JSON")):
        with pytest.raises(jl.JsonlError, match=match):
            jl.to_csv(BytesIO(data))
    monkeypatch.setattr(jl, "MAX_LINE_BYTES", 10)
    with pytest.raises(jl.JsonlError, match="longer than 10 bytes"):
        jl.to_csv(BytesIO(b'{"a": "0123456789"}\n'))
    monkeypatch.setattr(jl, "MAX_LINE_BYTES", 1 << 20)
    monkeypatch.setattr(jl, "MAX_COLUMNS", 2)
    with pytest.raises(jl.JsonlError, match="more than 2 columns"):
        jl.to_csv(_lines({"a": 1, "b": 2}, {"c": 3}))
    monkeypatch.setattr(jl, "MAX_ROWS", 1)
    with pytest.raises(jl.JsonlError, match="more than 1 lines"):
        jl.to_csv(_lines({"a": 1}, {"a": 2}))



def test_generated_jsonl_csv_is_bounded_and_input_is_reread(monkeypatch):
    source = BytesIO(b"".join(json.dumps({"case": i, "activity": "a"}).encode() + bytes([10]) for i in range(300)))
    calls = []
    original_seek = source.seek

    def count_seek(*args, **kwargs):
        calls.append(args)
        return original_seek(*args, **kwargs)

    source.seek = count_seek
    result = jl.to_csv(source)
    assert result["rows"] == 300
    assert result["csv"].startswith("case,activity" + chr(10))
    assert result["csv"].endswith("299,a" + chr(10))
    assert calls == [(0,)]  # second pass, without retaining the flattened record list
    monkeypatch.setattr(jl, "MAX_CSV_CHARS", 10)
    with pytest.raises(jl.JsonlError, match="supported response size"):
        jl.to_csv(_lines({"case": "x" * 50}))



def test_route_is_owner_only(tmp_path):
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
data=b'{"case":"o1","activity":"A","at":"2024-01-01T00:00:00Z"}\n{"case":"o2","activity":"A","at":"2024-01-02T00:00:00Z"}\n'
with TestClient(app) as client:
    owner={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
    bearer={'Authorization':'Bearer '+local_auth.ensure_api_token()}
    assert client.post('/v1/event-logs/jsonl-to-csv',content=data,headers=bearer).status_code in (401,403)
    r=client.post('/v1/event-logs/jsonl-to-csv',content=data,headers=owner)
    assert r.status_code==200 and r.headers['cache-control']=='no-store' and r.json()['rows']==2, r.text
    assert client.post('/v1/event-logs/jsonl-to-csv',content=b'[1]',headers=owner).status_code==422
'''
    env = {**os.environ, 'WORKFLOW_OBSERVER_DATA': str(tmp_path / 'data'), 'WORKFLOW_OBSERVER_AUTH_DIR': str(tmp_path / 'auth')}
    subprocess.run([sys.executable, '-c', code], env=env, check=True, timeout=60)
