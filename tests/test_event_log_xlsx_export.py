"""Excel export: the CSV export's rows as text cells, read back to the same CSV."""
from __future__ import annotations

import csv
import io
import os
import subprocess
import sys
import zipfile
from io import BytesIO

import pytest

from server import event_log_workspace as workspace
from server import event_log_xlsx as reader
from server import event_log_xlsx_export as xlsx_export
from server.event_log_workspace import EventLogWorkspaceError

MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time", "resource_column": "who"}


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()


def _log():
    rows = ["case,activity,time,who", '007,=SUM(1),2024-01-01T08:00:00Z,"Ann, <Lead> & co"',
            "007,Pay,2024-01-01T09:00:00Z,", "c2,Pay,2024-01-02T09:00:00Z,bob"]
    return workspace.import_log(BytesIO("\n".join(rows).encode()), name="x", source_format="csv", mapping=MAPPING)["log_id"]


def test_the_workbook_reads_back_to_the_csv_export(tmp_path):
    log = _log()
    path = tmp_path / "out.xlsx"
    r = xlsx_export.write(log, path)
    assert (r["rows"], r["columns"], r["control_characters_dropped"]) == (3, 4, 0)
    text = io.StringIO()
    workspace.export_log(log, "csv", text)
    with open(path, "rb") as f:
        back = reader.to_csv(f)
    assert back["csv"] == text.getvalue().replace("\r\n", "\n") and back["sheets"] == ["Events"]
    sheet = zipfile.ZipFile(path).read("xl/worksheets/sheet1.xml").decode()
    assert 'state="frozen"' in sheet and "<autoFilter" in sheet and "<f>" not in sheet       # text cells, no formulas
    assert 't="inlineStr"' in sheet and "&lt;Lead&gt; &amp; co" in sheet


def test_columns_beyond_z_and_the_row_limit(tmp_path, monkeypatch):
    assert [xlsx_export._column(i) for i in (0, 25, 26, 701, 702)] == ["A", "Z", "AA", "ZZ", "AAA"]
    log = _log()
    monkeypatch.setattr(xlsx_export, "MAX_ROWS", 3)
    monkeypatch.setattr(xlsx_export, "MAX_SHEETS", 1)
    with pytest.raises(EventLogWorkspaceError, match="more than 2 events"):
        xlsx_export.write(log, tmp_path / "big.xlsx")
    assert not (tmp_path / "big.xlsx").exists()


def test_route_is_owner_only(tmp_path):
    code = r'''
import csv
import io
from io import BytesIO
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth, event_log_workspace as workspace, event_log_xlsx as reader
rows = ["case,activity,time"] + [f"c{i},A,2024-03-0{1+i}T00:00:00Z" for i in range(3)]
log = workspace.import_log(BytesIO("\n".join(rows).encode()), name="q", source_format="csv",
    mapping={"case_column":"case","activity_column":"activity","timestamp_column":"time"})["log_id"]
with TestClient(app) as client:
    owner={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
    bearer={'Authorization':'Bearer '+local_auth.ensure_api_token()}
    url='/v1/event-logs/'+log+'/events.xlsx'
    assert client.get(url,headers=bearer).status_code in (401,403)
    r=client.get(url,headers=owner)
    assert r.status_code==200 and r.headers['cache-control']=='no-store' and r.content[:2]==b'PK', r.text
    assert reader.to_csv(io.BytesIO(r.content))['rows']==3
    assert client.get('/v1/event-logs/log_0000000000000000/events.xlsx',headers=owner).status_code==422
'''
    env = {**os.environ, 'WORKFLOW_OBSERVER_DATA': str(tmp_path / 'data'), 'WORKFLOW_OBSERVER_AUTH_DIR': str(tmp_path / 'auth')}
    subprocess.run([sys.executable, '-c', code], env=env, check=True, timeout=60)
    assert not list((tmp_path / 'data').glob('owg-xlsx-export-*'))


def test_matching_cases_workbook_reads_back_to_the_matching_csv(tmp_path, monkeypatch):
    from server.event_log_cohort_export import export_matching_cases
    log = _log()
    spec = {"cohort": {"activity": "Pay"}, "events_before": "2024-01-02T00:00:00Z"}
    r = xlsx_export.write_matching(log, spec, tmp_path / "m.xlsx")
    text = io.StringIO()
    export_matching_cases(log, spec, text, export_format="csv")
    with open(tmp_path / "m.xlsx", "rb") as f:
        back = reader.to_csv(f)
    assert back["csv"] == text.getvalue().replace("\r\n", "\n")
    assert r["rows"] == 2 and "c2" not in back["csv"] and "=SUM(1)" in back["csv"]
    with pytest.raises(EventLogWorkspaceError, match="Choose a case filter"):
        xlsx_export.write_matching(log, {"cohort": {}}, tmp_path / "none.xlsx")
    monkeypatch.setattr(xlsx_export, "MAX_ROWS", 2)
    monkeypatch.setattr(xlsx_export, "MAX_SHEETS", 1)
    with pytest.raises(EventLogWorkspaceError, match="the matching cases have more than 1 events"):
        xlsx_export.write_matching(log, spec, tmp_path / "big.xlsx")
    assert not (tmp_path / "big.xlsx").exists()


def test_matching_cases_route_is_owner_only(tmp_path):
    code = r'''
import csv
import io
from io import BytesIO
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth, event_log_workspace as workspace, event_log_xlsx as reader
rows = ["case,activity,time"] + [f"c{i},{'AB'[i % 2]},2024-03-0{1+i}T00:00:00Z" for i in range(3)]
log = workspace.import_log(BytesIO("\n".join(rows).encode()), name="q", source_format="csv",
    mapping={"case_column":"case","activity_column":"activity","timestamp_column":"time"})["log_id"]
with TestClient(app) as client:
    owner={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
    bearer={'Authorization':'Bearer '+local_auth.ensure_api_token()}
    url='/v1/event-logs/'+log+'/export/matching-cases.xlsx'
    body={'cohort':{'activity':'A'}}
    assert client.post(url,json=body,headers=bearer).status_code in (401,403)
    r=client.post(url,json=body,headers=owner)
    assert r.status_code==200 and r.headers['cache-control']=='no-store' and r.content[:2]==b'PK', r.text
    assert 'matching-cases.xlsx' in r.headers['content-disposition']
    assert reader.to_csv(io.BytesIO(r.content))['rows']==2
    assert client.post(url,json={'cohort':{}},headers=owner).status_code==422
    assert client.post(url,json={'cohort':{},'extra':1},headers=owner).status_code in (400,422)
    assert client.post(url,json={'cohort':{'activity':'Z'}},headers=owner).status_code==422
'''
    env = {**os.environ, 'WORKFLOW_OBSERVER_DATA': str(tmp_path / 'data'), 'WORKFLOW_OBSERVER_AUTH_DIR': str(tmp_path / 'auth')}
    subprocess.run([sys.executable, '-c', code], env=env, check=True, timeout=60)
    assert not list((tmp_path / 'data').glob('owg-xlsx-matching-*'))


def test_large_logs_continue_on_further_worksheets(tmp_path, monkeypatch):
    from server import event_log_xlsx as reader
    log = _log()
    text = io.StringIO()
    workspace.export_log(log, "csv", text)
    expected = list(csv.reader(io.StringIO(text.getvalue())))
    monkeypatch.setattr(xlsx_export, "MAX_ROWS", 3)   # a header and two events per worksheet
    result = xlsx_export.write(log, tmp_path / "split.xlsx")
    sheets = -(-(len(expected) - 1) // 2)
    assert result["worksheets"] == sheets and result["rows"] == len(expected) - 1
    names = ["Events"] + [f"Events {i}" for i in range(2, sheets + 1)]
    rows = []
    for index, name in enumerate(names):
        with open(tmp_path / "split.xlsx", "rb") as handle:
            back = list(csv.reader(io.StringIO(reader.to_csv(handle, name)["csv"])))
        assert back[0] == expected[0]
        rows += back[1:]
    assert rows == expected[1:]
