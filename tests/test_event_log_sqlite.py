"""Reading a table of a SQLite database file as CSV for the ordinary CSV import."""
from __future__ import annotations

import csv
import io
import os
import sqlite3
import subprocess
import sys
from io import BytesIO

import pytest

from server import event_log_sqlite as sq
from server import event_log_workspace as workspace


def _db(tmp_path, build) -> bytes:
    path = tmp_path / "src.db"
    conn = sqlite3.connect(path)
    build(conn)
    conn.commit()
    conn.close()
    return path.read_bytes()


def _orders(conn):
    conn.execute('CREATE TABLE "order events" (case_id TEXT, activity TEXT, at TEXT, amount REAL, blob BLOB, n INTEGER)')
    conn.executemany('INSERT INTO "order events" VALUES (?, ?, ?, ?, ?, ?)',
                     [(f"o{i}", a, f"2024-01-0{1 + i}T0{8 + j}:00:00Z", 9.5, b"\x00\x01" if i == 0 else None, None)
                      for i in range(3) for j, a in enumerate(["Create", "Ship"])])
    conn.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT)")
    conn.execute("INSERT INTO customers VALUES (1, '=cmd')")
    conn.execute('CREATE VIEW v AS SELECT * FROM customers')


def test_tables_are_offered_and_one_becomes_csv(tmp_path, monkeypatch):
    data = _db(tmp_path, _orders)
    first = sq.to_csv(BytesIO(data), folder=tmp_path)
    assert first["sheets"] == ["customers", "order events"] and first["sheet"] == "customers"   # views are not offered
    r = sq.to_csv(BytesIO(data), "order events", folder=tmp_path)
    rows = list(csv.reader(io.StringIO(r["csv"])))
    assert rows[0] == ["case_id", "activity", "at", "amount", "blob", "n"] and len(rows) == 7
    assert rows[1] == ["o0", "Create", "2024-01-01T08:00:00Z", "9.5", "", ""]
    assert (r["rows"], r["columns"], r["binary_cells_left_empty"], r["stored"]) == (6, 6, 2, False)
    assert list(tmp_path.glob("owg-sqlite-*")) == []                                    # temporary copy removed
    # The CSV goes through the ordinary import.
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)
    log = workspace.import_log(BytesIO(r["csv"].encode()), name="db", source_format="csv",
                               mapping={"case_column": "case_id", "activity_column": "activity", "timestamp_column": "at"})
    assert log["events"] == 6


def test_refusals(tmp_path, monkeypatch):
    with pytest.raises(sq.SqliteError, match="not a SQLite database"):
        sq.to_csv(BytesIO(b"PK\x03\x04 not a db"), folder=tmp_path)
    with pytest.raises(sq.SqliteError, match="no tables"):
        sq.to_csv(BytesIO(_db(tmp_path, lambda c: c.execute("CREATE VIEW only AS SELECT 1"))), folder=tmp_path)
    data = _db(tmp_path, _orders)
    with pytest.raises(sq.SqliteError, match="no table with this name"):
        sq.to_csv(BytesIO(data), "v", folder=tmp_path)
    with pytest.raises(sq.SqliteError, match="no table with this name"):
        sq.to_csv(BytesIO(data), 'customers"; DROP TABLE customers; --', folder=tmp_path)
    monkeypatch.setattr(sq, "MAX_ROWS", 5)
    with pytest.raises(sq.SqliteError, match="more than 5 rows"):
        sq.to_csv(BytesIO(data), "order events", folder=tmp_path)
    monkeypatch.setattr(sq, "MAX_COLUMNS", 3)
    with pytest.raises(sq.SqliteError, match="more than 3 columns"):
        sq.to_csv(BytesIO(data), "order events", folder=tmp_path)
    (tmp_path / "e").mkdir()
    empty = _db(tmp_path / "e", lambda c: c.execute("CREATE TABLE t (a)"))
    with pytest.raises(sq.SqliteError, match="no rows"):
        sq.to_csv(BytesIO(empty), folder=tmp_path)
    with pytest.raises(sq.SqliteError):
        sq.to_csv(BytesIO(sq._HEADER + b"\x00" * 200), folder=tmp_path)
    assert list(tmp_path.glob("owg-sqlite-*")) == []



def test_generated_csv_response_is_bounded_and_temporary_upload_is_removed(tmp_path, monkeypatch):
    data = _db(tmp_path, _orders)
    monkeypatch.setattr(sq, "MAX_CSV_CHARS", 30)
    with pytest.raises(sq.SqliteError, match="supported response size"):
        sq.to_csv(BytesIO(data), "order events", folder=tmp_path)
    assert list(tmp_path.glob("owg-sqlite-*")) == []
    # The header is also subject to the bound, including tables with wide columns.
    monkeypatch.setattr(sq, "MAX_CSV_CHARS", 3)
    with pytest.raises(sq.SqliteError, match="supported response size"):
        sq.to_csv(BytesIO(data), "order events", folder=tmp_path)



def test_a_slow_read_is_stopped(tmp_path, monkeypatch):
    def big(conn):
        conn.execute("CREATE TABLE t (a, b)")
        conn.executemany("INSERT INTO t VALUES (?, ?)", [(i, "x" * 50) for i in range(50_000)])
    data = _db(tmp_path, big)
    monkeypatch.setattr(sq, "MAX_SECONDS", 0)
    with pytest.raises(sq.SqliteError, match="longer than 0 seconds"):
        sq.to_csv(BytesIO(data), folder=tmp_path)


def test_a_hostile_schema_cannot_run_functions_through_a_trigger_or_view(tmp_path):
    def hostile(conn):
        conn.execute("CREATE TABLE t (a)")
        conn.execute("INSERT INTO t VALUES (1)")
        conn.execute("CREATE TRIGGER x AFTER INSERT ON t BEGIN SELECT load_extension('evil'); END")
    r = sq.to_csv(BytesIO(_db(tmp_path, hostile)), folder=tmp_path)
    assert r["sheets"] == ["t"] and r["rows"] == 1


def test_route_is_owner_only(tmp_path):
    data = _db(tmp_path, _orders)
    (tmp_path / "upload.db").write_bytes(data)
    code = r'''
import sys
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
data=open(sys.argv[1],'rb').read()
with TestClient(app) as client:
    owner={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
    bearer={'Authorization':'Bearer '+local_auth.ensure_api_token()}
    assert client.post('/v1/event-logs/sqlite-to-csv',content=data,headers=bearer).status_code in (401,403)
    r=client.post('/v1/event-logs/sqlite-to-csv?table=order%20events',content=data,headers=owner)
    assert r.status_code==200 and r.headers['cache-control']=='no-store' and r.json()['rows']==6, r.text
    assert client.post('/v1/event-logs/sqlite-to-csv',content=b'nope',headers=owner).status_code==422
'''
    env = {**os.environ, 'WORKFLOW_OBSERVER_DATA': str(tmp_path / 'data'), 'WORKFLOW_OBSERVER_AUTH_DIR': str(tmp_path / 'auth')}
    subprocess.run([sys.executable, '-c', code, str(tmp_path / "upload.db")], env=env, check=True, timeout=60)
