"""OCEL 2.0 SQLite databases are recognised and imported through the OCEL import."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from io import BytesIO

import pytest

from server import event_log_sqlite as sq
from server import event_log_workspace as workspace


def build(conn):
    conn.executescript("""
        CREATE TABLE event (ocel_id TEXT, ocel_type TEXT);
        CREATE TABLE object (ocel_id TEXT, ocel_type TEXT);
        CREATE TABLE event_object (ocel_event_id TEXT, ocel_object_id TEXT, ocel_qualifier TEXT);
        CREATE TABLE object_object (ocel_source_id TEXT, ocel_target_id TEXT, ocel_qualifier TEXT);
        CREATE TABLE event_map_type (ocel_type TEXT, ocel_type_map TEXT);
        CREATE TABLE object_map_type (ocel_type TEXT, ocel_type_map TEXT);
        INSERT INTO event_map_type VALUES ('create order', 'CreateOrder'), ('ship', 'Ship');
        INSERT INTO object_map_type VALUES ('order', 'Order'), ('item', 'Item');
        CREATE TABLE event_CreateOrder (ocel_id TEXT, ocel_time TIMESTAMP, channel TEXT);
        CREATE TABLE event_Ship (ocel_id TEXT, ocel_time TIMESTAMP);
        CREATE TABLE object_Order (ocel_id TEXT, ocel_time TIMESTAMP, ocel_changed_field TEXT, status TEXT, region TEXT);
        CREATE TABLE object_Item (ocel_id TEXT, ocel_time TIMESTAMP, ocel_changed_field TEXT);
    """)
    for i in range(3):
        conn.execute("INSERT INTO event VALUES (?, 'create order'), (?, 'ship')", [f"e{i}c", f"e{i}s"])
        conn.execute("INSERT INTO event_CreateOrder VALUES (?, ?, 'web')", [f"e{i}c", f"2024-01-0{1 + i} 08:00:00"])
        conn.execute("INSERT INTO event_Ship VALUES (?, ?)", [f"e{i}s", f"2024-01-0{1 + i}T09:00:00+00:00"])
        conn.execute("INSERT INTO object VALUES (?, 'order'), (?, 'item')", [f"o{i}", f"i{i}"])
        conn.execute("INSERT INTO object_Order VALUES (?, '1970-01-01 00:00:00', NULL, 'open', 'EU')", [f"o{i}"])
        conn.execute("INSERT INTO object_Order VALUES (?, ?, 'status', 'shipped', NULL)", [f"o{i}", f"2024-01-0{1 + i} 09:00:00"])
        conn.execute("INSERT INTO event_object VALUES (?, ?, 'order'), (?, ?, 'item'), (?, ?, 'order')",
                     [f"e{i}c", f"o{i}", f"e{i}c", f"i{i}", f"e{i}s", f"o{i}"])
        conn.execute("INSERT INTO object_object VALUES (?, ?, 'contains')", [f"o{i}", f"i{i}"])


def _db(tmp_path, fn=build) -> bytes:
    path = tmp_path / "ocel.sqlite"
    conn = sqlite3.connect(path)
    fn(conn)
    conn.commit()
    conn.close()
    return path.read_bytes()


def test_ocel_databases_are_flagged_and_converted(tmp_path, monkeypatch):
    data = _db(tmp_path)
    assert sq.to_csv(BytesIO(data), folder=tmp_path)["ocel"] is True
    r = sq.to_ocel_json(BytesIO(data), folder=tmp_path)
    assert (r["events"], r["objects"], r["events_without_time"], r["times_without_offset_read_as_utc"]) == (6, 6, 0, 9)
    doc = json.loads(r["document"])
    create = next(e for e in doc["events"] if e["id"] == "e0c")
    assert create["time"] == "2024-01-01T08:00:00Z" and create["attributes"] == [{"name": "channel", "value": "web"}]
    order = next(o for o in doc["objects"] if o["id"] == "o0")
    assert order["attributes"] == [{"name": "status", "time": "1970-01-01T00:00:00Z", "value": "open"},
                                   {"name": "region", "time": "1970-01-01T00:00:00Z", "value": "EU"},
                                   {"name": "status", "time": "2024-01-01T09:00:00Z", "value": "shipped"}]
    assert order["relationships"] == [{"objectId": "i0", "qualifier": "contains"}]
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)
    log = workspace.import_log(BytesIO(r["document"].encode()), name="ocel", source_format="ocel")
    assert log["events"] == 6 and log["objects"] == 6
    assert {t["object_type"] for t in workspace.object_types(log["log_id"])["object_types"]} == {"order", "item"}


def test_plain_databases_are_not_ocel(tmp_path):
    plain = _db(tmp_path, lambda c: (c.execute("CREATE TABLE t (a)"), c.execute("INSERT INTO t VALUES (1)")))
    assert "ocel" not in sq.to_csv(BytesIO(plain), folder=tmp_path)
    with pytest.raises(sq.SqliteError, match="not in the OCEL 2.0 SQLite format"):
        sq.to_ocel_json(BytesIO(plain), folder=tmp_path)


def test_limits(tmp_path, monkeypatch):
    data = _db(tmp_path)
    monkeypatch.setattr(sq, "MAX_OCEL_ROWS", 5)
    with pytest.raises(sq.SqliteError, match="more than 5 rows"):
        sq.to_ocel_json(BytesIO(data), folder=tmp_path)

    # Dynamic event/object type tables must share the same cumulative budget.
    monkeypatch.setattr(sq, "MAX_OCEL_ROWS", 7)
    with pytest.raises(sq.SqliteError, match="more than 7 rows"):
        sq.to_ocel_json(BytesIO(data), folder=tmp_path)

    def untimed(conn):
        build(conn)
        conn.execute("DELETE FROM event_CreateOrder")
        conn.execute("DELETE FROM event_Ship")
    with pytest.raises(sq.SqliteError, match="no events with a time"):
        monkeypatch.setattr(sq, "MAX_OCEL_ROWS", 5_000_000)
        (tmp_path / "u").mkdir()
        sq.to_ocel_json(BytesIO(_db(tmp_path / "u", untimed)), folder=tmp_path)
    assert list(tmp_path.glob("owg-sqlite-*")) == []


def test_route_is_owner_only(tmp_path):
    (tmp_path / "u.sqlite").write_bytes(_db(tmp_path))
    code = r'''
import sys
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
data=open(sys.argv[1],'rb').read()
with TestClient(app) as client:
    owner={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
    bearer={'Authorization':'Bearer '+local_auth.ensure_api_token()}
    assert client.post('/v1/event-logs/ocel-sqlite-to-json',content=data,headers=bearer).status_code in (401,403)
    r=client.post('/v1/event-logs/ocel-sqlite-to-json',content=data,headers=owner)
    assert r.status_code==200 and r.headers['cache-control']=='no-store' and r.json()['events']==6, r.text
    assert client.post('/v1/event-logs/ocel-sqlite-to-json',content=b'nope',headers=owner).status_code==422
'''
    env = {**os.environ, 'WORKFLOW_OBSERVER_DATA': str(tmp_path / 'data'), 'WORKFLOW_OBSERVER_AUTH_DIR': str(tmp_path / 'auth')}
    subprocess.run([sys.executable, '-c', code, str(tmp_path / "u.sqlite")], env=env, check=True, timeout=60)
