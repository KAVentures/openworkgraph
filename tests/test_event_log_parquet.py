"""Reading a Parquet file as CSV for the ordinary CSV import."""
from __future__ import annotations

import csv
import io
import os
import subprocess
import sys
from io import BytesIO

import duckdb
import pytest

from server import event_log_parquet as pq
from server import event_log_workspace as workspace


def _parquet(tmp_path, select: str) -> bytes:
    path = tmp_path / "src.parquet"
    duckdb.connect().execute(f"COPY ({select}) TO '{path}' (FORMAT parquet)")
    return path.read_bytes()


EVENTS = """SELECT 'o' || (i // 2) AS case_id, CASE WHEN i % 2 = 0 THEN 'Create' ELSE 'Ship' END AS activity,
    TIMESTAMPTZ '2024-01-01 08:00:00+00' + INTERVAL (i) HOUR AS at, 9.5::DECIMAL(5,2) AS amount,
    [1, 2] AS tags, {'tier': 'gold'} AS customer, CASE WHEN i = 0 THEN 'x'::BLOB END AS raw, NULL::INT AS empty,
    i % 3 = 0 AS flag, DATE '2024-01-01' AS day FROM range(6) t(i)"""


def test_rows_and_values(tmp_path, monkeypatch):
    r = pq.to_csv(BytesIO(_parquet(tmp_path, EVENTS)), folder=tmp_path)
    rows = list(csv.reader(io.StringIO(r["csv"])))
    assert rows[0] == ["case_id", "activity", "at", "amount", "tags", "customer", "raw", "empty", "flag", "day"]
    assert rows[1] == ["o0", "Create", "2024-01-01T08:00:00Z", "9.50", "[1,2]", '{"tier":"gold"}', "", "", "true", "2024-01-01"]
    assert (r["rows"], r["columns"], r["binary_cells_left_empty"], r["dates_have_no_offset"]) == (6, 10, 1, False)
    assert list(tmp_path.glob("owg-parquet-*")) == []
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)
    log = workspace.import_log(BytesIO(r["csv"].encode()), name="p", source_format="csv",
                               mapping={"case_column": "case_id", "activity_column": "activity", "timestamp_column": "at"})
    assert log["events"] == 6


def test_naive_timestamps_are_flagged(tmp_path):
    r = pq.to_csv(BytesIO(_parquet(tmp_path, "SELECT TIMESTAMP '2024-01-01 08:00:00' AS at")), folder=tmp_path)
    assert r["dates_have_no_offset"] is True and r["csv"].splitlines()[1] == "2024-01-01T08:00:00"


def test_refusals(tmp_path, monkeypatch):
    for data in (b"PAR1", b"not parquet at all", b"PAR1" + b"\x00" * 40 + b"PAR1"):
        with pytest.raises(pq.ParquetError):
            pq.to_csv(BytesIO(data), folder=tmp_path)
    with pytest.raises(pq.ParquetError, match="no rows"):
        pq.to_csv(BytesIO(_parquet(tmp_path, "SELECT 1 AS a WHERE false")), folder=tmp_path)
    data = _parquet(tmp_path, EVENTS)
    monkeypatch.setattr(pq, "MAX_ROWS", 5)
    with pytest.raises(pq.ParquetError, match="more than 5 rows"):
        pq.to_csv(BytesIO(data), folder=tmp_path)
    monkeypatch.setattr(pq, "MAX_COLUMNS", 3)
    with pytest.raises(pq.ParquetError, match="more than 3 columns"):
        pq.to_csv(BytesIO(data), folder=tmp_path)
    assert list(tmp_path.glob("owg-parquet-*")) == []


def test_route_is_owner_only(tmp_path):
    (tmp_path / "u.parquet").write_bytes(_parquet(tmp_path, EVENTS))
    code = r'''
import sys
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
data=open(sys.argv[1],'rb').read()
with TestClient(app) as client:
    owner={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
    bearer={'Authorization':'Bearer '+local_auth.ensure_api_token()}
    assert client.post('/v1/event-logs/parquet-to-csv',content=data,headers=bearer).status_code in (401,403)
    r=client.post('/v1/event-logs/parquet-to-csv',content=data,headers=owner)
    assert r.status_code==200 and r.headers['cache-control']=='no-store' and r.json()['rows']==6, r.text
    assert client.post('/v1/event-logs/parquet-to-csv',content=b'nope',headers=owner).status_code==422
'''
    env = {**os.environ, 'WORKFLOW_OBSERVER_DATA': str(tmp_path / 'data'), 'WORKFLOW_OBSERVER_AUTH_DIR': str(tmp_path / 'auth')}
    subprocess.run([sys.executable, '-c', code, str(tmp_path / "u.parquet")], env=env, check=True, timeout=60)
