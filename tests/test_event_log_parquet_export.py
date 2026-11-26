"""Parquet export: typed columns, the CSV export's rows, and a round trip through the Parquet import."""
from __future__ import annotations

import os
import subprocess
import sys
from io import BytesIO

import duckdb
import pytest

from server import event_log_parquet as parquet_import
from server import event_log_parquet_export as parquet_export
from server import event_log_workspace as workspace

MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time",
           "resource_column": "resource"}
ROWS = ('case,activity,time,resource\n'
        'c1,"Check, ""quoted""",2024-03-01T08:00:00+02:00,ann\n'
        'c1,Ship,2024-03-01T09:00:00.123456Z,\n'
        '007,Receive,2024-03-02T09:00:00Z,bob\n')


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()


def _log() -> str:
    return workspace.import_log(BytesIO(ROWS.encode()), name="orders", source_format="csv", mapping=MAPPING)["log_id"]


def test_typed_columns_and_values(tmp_path):
    out = tmp_path / "events.parquet"
    meta = parquet_export.write(_log(), out)
    assert meta == {"format": parquet_export.FORMAT, "rows": 3}
    assert not list(tmp_path.glob("*staging*"))
    conn = duckdb.connect()
    types = dict(conn.execute("SELECT column_name, column_type FROM (DESCRIBE SELECT * FROM read_parquet(?))",
                              [str(out)]).fetchall())
    assert types == {"case_id": "VARCHAR", "activity": "VARCHAR", "timestamp": "TIMESTAMP WITH TIME ZONE",
                     "resource": "VARCHAR"}
    rows = conn.execute("SELECT case_id, activity, strftime(timezone('UTC', \"timestamp\"), '%Y-%m-%dT%H:%M:%S.%fZ'), "
                        "resource FROM read_parquet(?) ORDER BY 3", [str(out)]).fetchall()
    assert rows == [("c1", 'Check, "quoted"', "2024-03-01T06:00:00.000000Z", "ann"),
                    ("c1", "Ship", "2024-03-01T09:00:00.123456Z", None),
                    ("007", "Receive", "2024-03-02T09:00:00.000000Z", "bob")]


def test_round_trip_through_parquet_import(tmp_path):
    log = _log()
    out = tmp_path / "events.parquet"
    parquet_export.write(log, out)
    with open(out, "rb") as handle:
        converted = parquet_import.to_csv(handle, folder=tmp_path / "data")
    assert converted["rows"] == 3


def test_unknown_log_is_refused_and_cleaned_up(tmp_path):
    out = tmp_path / "events.parquet"
    with pytest.raises(workspace.EventLogWorkspaceError):
        parquet_export.write("log_0000000000000000", out)
    assert not list(tmp_path.glob("*staging*")) and not out.exists()


def test_route_is_owner_only(tmp_path):
    code = r'''
import io
from io import BytesIO
import duckdb, tempfile, os
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth, event_log_workspace as workspace
rows = ["case,activity,time"] + [f"c{i},A,2024-03-0{1+i}T00:00:00Z" for i in range(3)]
log = workspace.import_log(BytesIO("\n".join(rows).encode()), name="q", source_format="csv",
    mapping={"case_column":"case","activity_column":"activity","timestamp_column":"time"})["log_id"]
with TestClient(app) as client:
    owner={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
    bearer={'Authorization':'Bearer '+local_auth.ensure_api_token()}
    url='/v1/event-logs/'+log+'/events.parquet'
    assert client.get(url,headers=bearer).status_code in (401,403)
    r=client.get(url,headers=owner)
    assert r.status_code==200 and r.headers['cache-control']=='no-store' and r.content[:4]==b'PAR1', r.text
    assert r.headers['x-owg-rows']=='3' and 'attachment' in r.headers['content-disposition']
    path=os.path.join(tempfile.mkdtemp(),'e.parquet'); open(path,'wb').write(r.content)
    assert duckdb.connect().execute("SELECT count(*) FROM read_parquet(?)",[path]).fetchone()[0]==3
    assert client.get('/v1/event-logs/log_0000000000000000/events.parquet',headers=owner).status_code==422
print("ok")
'''
    env = {**os.environ, 'WORKFLOW_OBSERVER_DATA': str(tmp_path / 'data'), 'WORKFLOW_OBSERVER_AUTH_DIR': str(tmp_path / 'auth'),
           'WORKFLOW_OBSERVER_CONFIG': str(tmp_path / 'config.json')}
    done = subprocess.run([sys.executable, '-c', code], env=env, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0 and done.stdout.strip().endswith("ok"), done.stderr
    assert not list((tmp_path / 'data').glob('owg-parquet-export-*'))
