"""OCEL 2.0 SQLite export: the standard tables with the same content as the OCEL 2.0 JSON export."""
from __future__ import annotations

import io
import json
import os
import sqlite3
import subprocess
import sys
from io import BytesIO

import pytest

from server import event_log_ocel_sqlite as ocel_sqlite
from server import event_log_sample as sample
from server import event_log_workspace as workspace


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()


def test_the_sample_log_round_trips_into_the_standard_tables(tmp_path):
    sample.import_sample()
    log = workspace.list_logs()["logs"][0]["log_id"]
    buf = io.StringIO()
    workspace.export_log(log, "ocel", buf)
    doc = json.loads(buf.getvalue())
    path = tmp_path / "out.sqlite"
    r = ocel_sqlite.write(log, path)
    assert (r["events"], r["objects"]) == (len(doc["events"]), len(doc["objects"]))
    conn = sqlite3.connect(path)
    tables = {t for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"event", "object", "event_object", "object_object", "event_map_type", "object_map_type"} <= tables
    assert conn.execute("SELECT count(*) FROM event").fetchone()[0] == len(doc["events"])
    links = sum(len(e["relationships"]) for e in doc["events"])
    assert conn.execute("SELECT count(*) FROM event_object").fetchone()[0] == links
    o2o = sum(len(o["relationships"]) for o in doc["objects"])
    assert conn.execute("SELECT count(*) FROM object_object").fetchone()[0] == o2o
    # Every event type has its table; each event appears there once with its time.
    for ocel_type, suffix in conn.execute("SELECT ocel_type, ocel_type_map FROM event_map_type").fetchall():
        n = conn.execute(f'SELECT count(*) FROM "event_{suffix}"').fetchone()[0]
        assert n == sum(e["type"] == ocel_type for e in doc["events"])
    first = doc["events"][0]
    suffix = conn.execute("SELECT ocel_type_map FROM event_map_type WHERE ocel_type = ?", [first["type"]]).fetchone()[0]
    assert conn.execute(f'SELECT ocel_time FROM "event_{suffix}" WHERE ocel_id = ?', [first["id"]]).fetchone()[0] == first["time"]
    # Object attribute rows: one initial row per object plus one per timed value.
    obj = next(o for o in doc["objects"] if o["attributes"])
    suffix = conn.execute("SELECT ocel_type_map FROM object_map_type WHERE ocel_type = ?", [obj["type"]]).fetchone()[0]
    rows = conn.execute(f'SELECT ocel_time, ocel_changed_field FROM "object_{suffix}" WHERE ocel_id = ? ORDER BY 1', [obj["id"]]).fetchall()
    timed = [a for a in obj["attributes"] if a["time"] != "1970-01-01T00:00:00Z"]
    assert rows[0] == ("1970-01-01T00:00:00Z", None) and len(rows) == 1 + len(timed)


def test_type_names_become_unique_safe_table_names():
    assert ocel_sqlite._table_names(["create order", "Create-Order", "", "x\"; DROP"]) == {
        "create order": "CreateOrder", "Create-Order": "CreateOrder2", "": "Type", "x\"; DROP": "XDrop"}



@pytest.mark.parametrize(
    "kind,names",
    [
        ("event", ["ocel_id"]),
        ("event", ["OCEL_TIME"]),
        ("object", ["ocel_changed_field"]),
        ("object", ["ocel_id"]),
        ("event", ["Attribute", "attribute"]),
        ("object", ["Flag", "flag"]),
    ],
)
def test_sqlite_attribute_column_collisions_refuse_before_writing(tmp_path, monkeypatch, kind, names):
    doc = {"events": [], "objects": []}
    attributes = [{"name": name, "value": "value", "time": "1970-01-01T00:00:00Z"} for name in names]
    if kind == "event":
        doc["events"] = [{"id": "e1", "type": "Activity", "time": "2024-01-01T00:00:00Z",
                           "attributes": attributes, "relationships": []}]
    else:
        doc["objects"] = [{"id": "o1", "type": "Order",
                            "attributes": attributes, "relationships": []}]

    def export(_log_id, source_format, target):
        assert source_format == "ocel"
        target.write(json.dumps(doc))

    monkeypatch.setattr(workspace, "export_log", export)
    path = tmp_path / "conflict.sqlite"
    with pytest.raises(workspace.EventLogWorkspaceError, match="OCEL SQLite attribute column"):
        ocel_sqlite.write("log_test", path)
    assert not path.exists()


def test_repeating_same_attribute_on_multiple_events_is_valid(tmp_path, monkeypatch):
    doc = {"objects": [], "events": [
        {"id": event_id, "type": "Activity", "time": "2024-01-01T00:00:00Z",
         "attributes": [{"name": "same", "value": event_id}], "relationships": []}
        for event_id in ("e1", "e2")
    ]}

    def export(_log_id, source_format, target):
        assert source_format == "ocel"
        target.write(json.dumps(doc))

    monkeypatch.setattr(workspace, "export_log", export)
    path = tmp_path / "valid.sqlite"
    assert ocel_sqlite.write("log_test", path)["events"] == 2
    conn = sqlite3.connect(path)
    try:
        assert conn.execute('SELECT COUNT(*), COUNT("same") FROM event_Activity').fetchone() == (2, 2)
    finally:
        conn.close()


def test_route_is_owner_only(tmp_path):
    code = r'''
import sqlite3, tempfile
from io import BytesIO
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth, event_log_workspace as workspace
rows = ["case,activity,time"] + [f"c{i},A,2024-03-0{1+i}T00:00:00Z" for i in range(3)]
log = workspace.import_log(BytesIO("\n".join(rows).encode()), name="q", source_format="csv",
    mapping={"case_column":"case","activity_column":"activity","timestamp_column":"time"})["log_id"]
with TestClient(app) as client:
    owner={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
    bearer={'Authorization':'Bearer '+local_auth.ensure_api_token()}
    url='/v1/event-logs/'+log+'/ocel.sqlite'
    assert client.get(url,headers=bearer).status_code in (401,403)
    r=client.get(url,headers=owner)
    assert r.status_code==200 and r.headers['cache-control']=='no-store' and r.content.startswith(b'SQLite format 3'), r.text
    path=tempfile.mktemp(suffix='.sqlite'); open(path,'wb').write(r.content)
    assert sqlite3.connect(path).execute('SELECT count(*) FROM event').fetchone()[0]==3
    assert client.get('/v1/event-logs/log_0000000000000000/ocel.sqlite',headers=owner).status_code==422
'''
    env = {**os.environ, 'WORKFLOW_OBSERVER_DATA': str(tmp_path / 'data'), 'WORKFLOW_OBSERVER_AUTH_DIR': str(tmp_path / 'auth')}
    subprocess.run([sys.executable, '-c', code], env=env, check=True, timeout=60)
    assert not list((tmp_path / 'data').glob('owg-ocel-sqlite-*'))
