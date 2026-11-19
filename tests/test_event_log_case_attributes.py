"""Joining a CSV case table onto a saved log's cases."""
from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path

import pytest

from server import event_log_case_attributes as case_attributes
from server import event_log_cohorts as cohorts
from server import event_log_quality as quality
from server import event_log_workspace as workspace

MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time"}


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _log(cases=6):
    rows = ["case,activity,time"]
    for i in range(cases):
        rows += [f"c{i},Receive,2024-03-0{1 + i}T08:00:00Z", f"c{i},Pay,2024-03-0{1 + i}T{9 + i:02d}:00:00Z"]
    return workspace.import_log(BytesIO("\n".join(rows).encode()), name="t", source_format="csv", mapping=MAPPING)["log_id"]


def _table(tmp_path, text, name="cases.csv"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _attributes(log_id):
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            return {o: json.loads(a) for o, a in conn.execute(
                "SELECT object_id, attributes FROM objects WHERE log_id = ? ORDER BY 1", [log_id]).fetchall()}
        finally:
            conn.close()


def test_adds_columns_to_matching_cases_and_feeds_cohorts(tmp_path):
    log = _log()
    digest_before = quality.quality(log)["current"]["content_sha256"]
    table = _table(tmp_path, "Case ID;Region;Segment;Notes\nc0;EU;retail;x\nc1;EU;;y\nc2;US;wholesale;\n"
                             "c3; US ;retail;\nzz;EU;retail;\n;EU;retail;\n")
    record = case_attributes.add(log, table, id_column="Case ID", columns=["Region", "Segment"], delimiter=";")
    assert record["cases_updated"] == 4 and record["cases_without_row"] == 2
    assert record["rows"] == 6 and record["rows_not_in_log"] == 1 and record["rows_without_id"] == 1
    assert record["values_added"] == {"Region": 4, "Segment": 3}
    assert len(record["source_sha256"]) == 64 and record["upload_id"].startswith("ca_")
    attrs = _attributes(log)
    assert attrs["c1"] == {"Region": "EU"}  # empty cells add nothing
    assert attrs["c3"] == {"Region": "US", "Segment": "retail"}  # values are trimmed
    assert attrs["c4"] == {} and "Notes" not in attrs["c0"]
    # Cohort filters use the new attributes like any object attribute.
    def eu(conn):
        cohorts.restrict(conn, cohorts.parse_cohort({"attribute": "Region=EU"}), log)
        return {"cases": conn.execute("SELECT count(DISTINCT case_id) FROM ce").fetchone()[0]}
    assert workspace._query(log, None, eu)["cases"] == 2
    # Events and the content digest are untouched; the upload is listed.
    assert quality.quality(log)["current"]["content_sha256"] == digest_before
    assert [u["upload_id"] for u in case_attributes.uploads(log)["uploads"]] == [record["upload_id"]]


def test_refuses_ambiguous_conflicting_and_unmatched_tables(tmp_path):
    log = _log()
    with pytest.raises(workspace.EventLogWorkspaceError, match="more than once"):
        case_attributes.add(log, _table(tmp_path, "id,Region\nc0,EU\nc0,US\n"), id_column="id", columns=["Region"])
    with pytest.raises(workspace.EventLogWorkspaceError, match="no row"):
        case_attributes.add(log, _table(tmp_path, "id,Region\nx,EU\n"), id_column="id", columns=["Region"])
    with pytest.raises(workspace.EventLogWorkspaceError, match="missing columns"):
        case_attributes.add(log, _table(tmp_path, "id,Region\nc0,EU\n"), id_column="id", columns=["Nope"])
    with pytest.raises(workspace.EventLogWorkspaceError, match="id column cannot"):
        case_attributes.add(log, _table(tmp_path, "id,Region\nc0,EU\n"), id_column="id", columns=["id"])
    with pytest.raises(workspace.EventLogWorkspaceError, match="at least one"):
        case_attributes.add(log, _table(tmp_path, "id,Region\nc0,EU\n"), id_column="id", columns=[])
    case_attributes.add(log, _table(tmp_path, "id,Region\nc0,EU\n"), id_column="id", columns=["Region"])
    with pytest.raises(workspace.EventLogWorkspaceError, match="already have 'Region'"):
        case_attributes.add(log, _table(tmp_path, "id,Region\nc0,US\n"), id_column="id", columns=["Region"])
    replaced = case_attributes.add(log, _table(tmp_path, "id,Region\nc0,US\n"), id_column="id", columns=["Region"],
                                   replace=True)
    assert replaced["replaced_existing"] and _attributes(log)["c0"] == {"Region": "US"}
    wide = ",".join(f"a{i}" for i in range(33))
    with pytest.raises(workspace.EventLogWorkspaceError, match="at most 32"):
        case_attributes.add(log, _table(tmp_path, "id," + wide + "\nc0," + ",".join("v" * 33) + "\n"),
                            id_column="id", columns=wide.split(","))
    # 31 new names plus the existing Region would exceed nothing; 32 new names plus Region would.
    names = [f"a{i}" for i in range(32)]
    with pytest.raises(workspace.EventLogWorkspaceError, match="more than 32 attribute names"):
        case_attributes.add(log, _table(tmp_path, "id," + ",".join(names) + "\nc0," + ",".join("v" * 32) + "\n"),
                            id_column="id", columns=names)


def test_remove_undoes_only_what_an_upload_added(tmp_path):
    log = _log()
    first = case_attributes.add(log, _table(tmp_path, "id,Region,Channel\nc0,EU,web\nc1,US,phone\n"),
                                id_column="id", columns=["Region", "Channel"])
    with pytest.raises(workspace.EventLogWorkspaceError, match="only columns"):
        case_attributes.remove(log, {"upload_id": first["upload_id"], "columns": ["Other"]})
    with pytest.raises(workspace.EventLogWorkspaceError, match="unknown"):
        case_attributes.remove(log, {"upload_id": "ca_0000000000000000"})
    out = case_attributes.remove(log, {"upload_id": first["upload_id"], "columns": ["Channel"]})
    assert out["cases_changed"] == 2 and _attributes(log)["c0"] == {"Region": "EU"}
    assert case_attributes.uploads(log)["uploads"][0]["columns"] == ["Region"]
    case_attributes.remove(log, {"upload_id": first["upload_id"]})
    assert _attributes(log)["c0"] == {} and case_attributes.uploads(log)["uploads"] == []


def test_ocel_needs_an_object_type_and_keeps_typed_values(tmp_path):
    doc = {"objectTypes": [{"name": "order", "attributes": []}, {"name": "item", "attributes": []}],
           "eventTypes": [{"name": "Create", "attributes": []}],
           "objects": [{"id": "o1", "type": "order", "attributes": [{"name": "amount", "time": "2024-01-01T00:00:00Z", "value": 5}]},
                       {"id": "i1", "type": "item", "attributes": []}],
           "events": [{"id": "e1", "type": "Create", "time": "2024-01-01T00:00:00Z", "attributes": [],
                       "relationships": [{"objectId": "o1", "qualifier": ""}, {"objectId": "i1", "qualifier": ""}]}]}
    log = workspace.import_log(BytesIO(json.dumps(doc).encode()), name="o", source_format="ocel")["log_id"]
    table = _table(tmp_path, "order,priority\no1,high\ni1,low\n")
    with pytest.raises(workspace.EventLogWorkspaceError, match="choose the object type"):
        case_attributes.add(log, table, id_column="order", columns=["priority"])
    record = case_attributes.add(log, table, id_column="order", columns=["priority"], object_type="order")
    assert record["cases_updated"] == 1 and record["rows_not_in_log"] == 1
    assert _attributes(log)["o1"]["priority"] == "high" and _attributes(log)["i1"] == {}


def test_deleting_the_log_deletes_uploads(tmp_path):
    log = _log()
    case_attributes.add(log, _table(tmp_path, "id,Region\nc0,EU\n"), id_column="id", columns=["Region"])
    workspace.delete_log(log)
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            assert conn.execute("SELECT count(*) FROM case_attribute_uploads").fetchone()[0] == 0
            assert conn.execute("SELECT count(*) FROM case_attribute_upload_changes").fetchone()[0] == 0
        finally:
            conn.close()


def test_routes(tmp_path):
    import os
    import subprocess
    import sys
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
rows = "case,activity,time\n" + "\n".join(f"c{i},A,2024-03-04T08:00:0{i}Z" for i in range(3))
with TestClient(app) as client:
    owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    store = {**owner, "X-OWG-Local-Storage-Confirmed": "true"}
    log_id = client.post("/v1/event-logs/import?format=csv&name=t&case_column=case&activity_column=activity&timestamp_column=time",
                         content=rows.encode(), headers=store).json()["log_id"]
    path = f"/v1/event-logs/{log_id}/case-attributes"
    body = b"id,Region\nc0,EU\nc1,US\n"
    assert client.post(path + "?id_column=id&column=Region", content=body, headers=bearer).status_code == 403
    assert client.post(path + "?id_column=id&column=Region", content=body, headers=owner).status_code == 403
    query = "?id_column=id&column=Region"
    assert client.post(path + query + "&preview=true", content=body, headers=bearer).status_code == 403
    reviewed = client.post(path + query + "&preview=true", content=body, headers=owner)
    assert reviewed.status_code == 200 and reviewed.json()["cases_updated"] == 2
    assert client.get(path, headers=owner).json()["uploads"] == []
    query += "&expected_preview_sha256=" + reviewed.json()["preview_sha256"]
    assert client.post(path + query, content=body, headers=owner).status_code == 403

    r = client.post(path + query, content=body, headers=store)
    assert r.status_code == 200 and r.json()["cases_updated"] == 2, r.text
    assert client.post(path + "?id_column=id&column=Region", content=body, headers=store).status_code == 422
    listed = client.get(path, headers=owner).json()["uploads"]
    assert client.get(path, headers=bearer).status_code == 403
    r = client.post(path + "/remove", json={"upload_id": listed[0]["upload_id"]}, headers=owner)
    assert r.status_code == 200 and r.json()["cases_changed"] == 2, r.text
print("ok")
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    done = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1], env=env,
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0 and done.stdout.strip().endswith("ok"), f"stdout={done.stdout}\nstderr={done.stderr}"



def test_undo_restores_previous_values_and_leaves_unmodified_cases(tmp_path):
    log = _log()
    conn = workspace._connect()
    try:
        conn.execute("UPDATE objects SET attributes=? WHERE log_id=? AND object_id='c0'",
                     [json.dumps({"Region": 7, "Segment": None, "Other": True}), log])
        conn.execute("UPDATE objects SET attributes=? WHERE log_id=? AND object_id='c1'",
                     [json.dumps({"Region": "untouched"}), log])
    finally:
        conn.close()
    old = _attributes(log)
    upload = case_attributes.add(log, _table(tmp_path, "id,Region,Segment\nc0,EU,retail\nc2,US,\n"),
                                 id_column="id", columns=["Region", "Segment"], replace=True)
    assert case_attributes.remove(log, {"upload_id": upload["upload_id"]})["cases_changed"] == 2
    assert _attributes(log) == old


def test_newer_overlap_blocks_undo_even_if_value_is_the_same(tmp_path):
    log = _log()
    first = case_attributes.add(log, _table(tmp_path, "id,Region,Channel\nc0,EU,web\n"),
                                id_column="id", columns=["Region", "Channel"])
    second = case_attributes.add(log, _table(tmp_path, "id,Region\nc0,EU\nc1,US\n"),
                                 id_column="id", columns=["Region"], replace=True)
    before = _attributes(log)
    with pytest.raises(workspace.EventLogWorkspaceError, match="newer upload"):
        case_attributes.remove(log, {"upload_id": first["upload_id"]})
    assert _attributes(log) == before
    case_attributes.remove(log, {"upload_id": first["upload_id"], "columns": ["Channel"]})
    case_attributes.remove(log, {"upload_id": second["upload_id"]})
    assert _attributes(log)["c0"] == {"Region": "EU"}
    assert _attributes(log)["c1"] == {}
    case_attributes.remove(log, {"upload_id": first["upload_id"]})
    assert _attributes(log)["c0"] == {}


def test_legacy_upload_cannot_remove_values_without_provenance(tmp_path):
    log = _log()
    record = case_attributes.add(log, _table(tmp_path, "id,Region\nc0,EU\n"),
                                 id_column="id", columns=["Region"])
    record.pop("undo_supported")
    conn = workspace._connect()
    try:
        conn.execute("DELETE FROM case_attribute_upload_changes WHERE log_id=?", [log])
        conn.execute("UPDATE case_attribute_uploads SET record=? WHERE log_id=?", [json.dumps(record), log])
    finally:
        conn.close()
    assert case_attributes.uploads(log)["uploads"][0]["undo_supported"] is False
    with pytest.raises(workspace.EventLogWorkspaceError, match="older upload"):
        case_attributes.remove(log, {"upload_id": record["upload_id"]})
    assert _attributes(log)["c0"] == {"Region": "EU"}


def test_preview_counts_overwrites_and_prevents_stale_apply(tmp_path):
    log = _log()
    kwargs = {"id_column": "id", "columns": ["Region"]}
    case_attributes.add(log, _table(tmp_path, "id,Region\nc0,SENSITIVE_OLD\n"), **kwargs)
    candidate = _table(tmp_path, "id,Region\nc0,SENSITIVE_NEW\nc1,APAC\nz,Unmatched\n", "candidate.csv")
    before = _attributes(log)
    blocked = case_attributes.add(log, candidate, preview=True, **kwargs)
    assert not blocked["can_apply"] and blocked["values_to_overwrite"] == {"Region": 1}
    reviewed = case_attributes.add(log, candidate, preview=True, replace=True, **kwargs)
    assert reviewed["can_apply"] and reviewed["cases_updated"] == 2 and reviewed["rows_not_in_log"] == 1
    assert reviewed["values_added"] == {"Region": 2}
    assert "SENSITIVE" not in json.dumps(reviewed) and _attributes(log) == before
    assert len(case_attributes.uploads(log)["uploads"]) == 1

    changed_file = _table(tmp_path, "id,Region\nc0,CHANGED\nc1,APAC\nz,Unmatched\n", "changed.csv")
    with pytest.raises(workspace.EventLogWorkspaceError, match="Preview the upload again"):
        case_attributes.add(log, changed_file, replace=True, expected_preview_sha256=reviewed["preview_sha256"], **kwargs)
    assert _attributes(log) == before

    case_attributes.add(log, _table(tmp_path, "id,Region\nc0,CHANGED\n"), replace=True, **kwargs)
    with pytest.raises(workspace.EventLogWorkspaceError, match="Preview the upload again"):
        case_attributes.add(log, candidate, replace=True, expected_preview_sha256=reviewed["preview_sha256"], **kwargs)
    assert _attributes(log)["c0"] == {"Region": "CHANGED"}
    refreshed = case_attributes.add(log, candidate, preview=True, replace=True, **kwargs)
    record = case_attributes.add(log, candidate, replace=True,
                                 expected_preview_sha256=refreshed["preview_sha256"], **kwargs)
    assert record["cases_updated"] == 2 and _attributes(log)["c0"] == {"Region": "SENSITIVE_NEW"}
