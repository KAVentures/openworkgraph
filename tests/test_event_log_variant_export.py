"""Every variant as CSV with shares, running share and durations."""
from __future__ import annotations

import csv
import io
import os
import subprocess
import sys
from io import BytesIO

import pytest

from server import event_log_variant_export as ve
from server import event_log_workspace as workspace
from server.event_log_workspace import EventLogWorkspaceError

MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time"}


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()


def _import(paths):
    rows = ["case,activity,time"]
    for i, (path, hours) in enumerate(paths):
        for j, a in enumerate(path):
            rows.append(f"c{i},{a},2024-01-{1 + i % 28:02d}T{j * hours:02d}:00:00Z")
    return workspace.import_log(BytesIO("\n".join(rows).encode()), name="v", source_format="csv", mapping=MAPPING)["log_id"]


def _read(text):
    return list(csv.DictReader(io.StringIO(text)))


@pytest.mark.parametrize(("raw", "expected"), [
    ("=1+1", "'=1+1"),
    ("  =SUM(1,2)", "'  =SUM(1,2)"),
    ("\ufeff+HYPERLINK(1)", "'\ufeff+HYPERLINK(1)"),
    (" \ufeff\t@unsafe", "' \ufeff\t@unsafe"),
    ("\n=1+1", "'\n=1+1"),
    ("\tplain", "'\tplain"),
    ("Normal activity", "Normal activity"),
    ("123", "123"),
])
def test_formula_like_variant_cells_are_literal_csv(raw, expected):
    assert ve._cell(raw) == expected
    stream = io.StringIO()
    csv.writer(stream).writerow([ve._cell(raw)])
    assert next(csv.reader(io.StringIO(stream.getvalue())))[0] == expected


def test_formula_like_activity_paths_are_safe_end_to_end():
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(["case", "activity", "time"])
    labels = ("  =SUM(1,2)", "\ufeff+HYPERLINK(1)", "Normal")
    for i, label in enumerate(labels):
        writer.writerow([f"c{i}", label, f"2024-01-0{i + 1}T00:00:00Z"])
    log = workspace.import_log(BytesIO(stream.getvalue().encode("utf-8")),
                               name="formula-prefixes", source_format="csv", mapping=MAPPING)["log_id"]
    text, meta = ve.variants_csv(log)
    assert meta["cases"] == 3
    assert {row["path"] for row in _read(text)} == {
        "'  =SUM(1,2)", "'\ufeff+HYPERLINK(1)", "Normal",
    }


def test_rows_shares_and_durations():
    log = _import([(["A", "B", "C"], 1)] * 6 + [(["A", "C"], 2)] * 3 + [(["=X", "B", "C"], 1)])
    text, meta = ve.variants_csv(log)
    rows = _read(text)
    assert meta == {"cases": 10, "variants": 3, "written": 3, "truncated": False}
    assert [r["path"] for r in rows] == ["A > B > C", "A > C", "'=X > B > C"]
    assert [r["cases"] for r in rows] == ["6", "3", "1"] and [r["steps"] for r in rows] == ["3", "2", "3"]
    assert rows[0]["share"] == "0.6" and rows[1]["cumulative_share"] == "0.9" and rows[2]["cumulative_share"] == "1.0"
    assert rows[0]["median_seconds"] == "7200.0" and rows[1]["median_seconds"] == "7200.0"


def test_cohort_truncation_and_empty(monkeypatch):
    log = _import([(["A", "B"], 1)] * 2 + [(["A", "C"], 1)] * 2 + [(["A", "D"], 1)])
    only_c = _read(ve.variants_csv(log, cohort={"activity": "C"})[0])
    assert [r["path"] for r in only_c] == ["A > C"]
    monkeypatch.setattr(ve, "MAX_VARIANTS", 2)
    text, meta = ve.variants_csv(log)
    assert meta["truncated"] is True and meta["written"] == 2 and len(_read(text)) == 2
    with pytest.raises(EventLogWorkspaceError):
        ve.variants_csv(log, cohort={"activity": "Nope"})


def test_route(tmp_path):
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
doc = "case,activity,time\n" + "".join(f"c{i},A,2024-01-0{i+1}T00:00:00Z\nc{i},{'B' if i%2 else 'C'},2024-01-0{i+1}T01:00:00Z\n" for i in range(5))
with TestClient(app) as client:
    owner={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
    made=client.post('/v1/event-logs/import?format=csv&name=q&case_column=case&activity_column=activity&timestamp_column=time',
        content=doc.encode(),headers={**owner,'x-owg-local-storage-confirmed':'true'})
    url='/v1/event-logs/'+made.json()['log_id']+'/variants.csv'
    assert client.get(url).status_code in (401,403)
    r=client.get(url,headers=owner)
    assert r.status_code==200 and r.headers['content-type'].startswith('text/csv'), r.text
    assert r.headers['x-owg-variants']=='variants=2; written=2; truncated=false'
    assert r.text.splitlines()[1].startswith('1,A > C,2,3,0.6,0.6,')
    assert client.get(url+'?activity=B',headers=owner).text.count('\n')==2
    exact=client.get(url+'?activity=B&exact_paths=true',headers=owner)
    assert exact.status_code==200 and exact.text.splitlines()[0].endswith(',path_json')
    assert exact.text.count('\n')==2 and exact.headers['x-owg-variants'].startswith('variants=1;')
'''
    env = {**os.environ, 'WORKFLOW_OBSERVER_DATA': str(tmp_path / 'data'), 'WORKFLOW_OBSERVER_AUTH_DIR': str(tmp_path / 'auth')}
    subprocess.run([sys.executable, '-c', code], env=env, check=True, timeout=60)
