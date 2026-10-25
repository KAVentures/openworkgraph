"""Temporal object attributes in object-centric logs (#370 R24)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO, StringIO
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import event_log_object_attributes as attributes
from server import event_log_workspace as workspace
from shared import event_log_formats as formats

ROOT = Path(__file__).resolve().parents[1]
T0 = "1970-01-01T00:00:00Z"


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _value(name, time, value):
    return {"name": name, "time": time, "value": value}


def _orders():
    """Three orders whose status is recorded over time, linked to events computed by hand below.

    o1: created (initial) -> approved 03:00 -> shipped 05:00, events at 01:00, 03:00 and 06:00.
        The stored value is the first listed, "created": the 03:00 and 06:00 links see another value.
    o2: status first recorded at 04:00, one event at 02:00 before it.
    o3: two different values recorded for 01:00, one event at 02:00 (ambiguous).
    """
    return {
        "objectTypes": [{"name": "order", "attributes": []}], "eventTypes": [],
        "objects": [
            {"id": "o1", "type": "order", "attributes": [_value("status", T0, "created"),
                                                         _value("status", "2024-01-01T05:00:00Z", "shipped"),
                                                         _value("status", "2024-01-01T03:00:00+00:00", "approved"),
                                                         _value("region", T0, "EU")]},
            {"id": "o2", "type": "order", "attributes": [_value("status", "2024-01-01T04:00:00Z", "approved"),
                                                         _value("status", "not a time", "lost")]},
            {"id": "o3", "type": "order", "attributes": [_value("status", "2024-01-01T01:00:00Z", "created"),
                                                         _value("status", "2024-01-01T01:00:00Z", "approved")]},
        ],
        "events": [
            {"id": "e1", "type": "Place", "time": "2024-01-01T01:00:00Z", "relationships": [{"objectId": "o1", "qualifier": ""}]},
            {"id": "e2", "type": "Approve", "time": "2024-01-01T03:00:00Z", "relationships": [{"objectId": "o1", "qualifier": ""}]},
            {"id": "e3", "type": "Pay", "time": "2024-01-01T06:00:00Z", "relationships": [{"objectId": "o1", "qualifier": ""}]},
            {"id": "e4", "type": "Place", "time": "2024-01-01T02:00:00Z",
             "relationships": [{"objectId": "o2", "qualifier": ""}, {"objectId": "o3", "qualifier": ""}]},
        ],
    }


def _import(doc, fmt="ocel"):
    return workspace.import_log(BytesIO(json.dumps(doc).encode()), name="orders", source_format=fmt)


def _status(result):
    order = {t["object_type"]: t for t in result["object_types"]}["order"]
    return {a["name"]: a for a in order["attributes"]}["status"]


def _utc(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)


def test_readers_keep_every_timed_value_and_the_first_listed_value():
    stats = formats.ReadStats()
    records = [r for r in formats.read_ocel_json(BytesIO(json.dumps(_orders()).encode()), stats=stats)
               if isinstance(r, formats.ObjectRecord)]
    o1 = records[0]
    assert o1.attributes == {"status": "created", "region": "EU"}  # unchanged: first listed value per name
    assert sorted((n, v) for n, _, v in o1.history) == [("region", "EU"), ("status", "approved"),
                                                         ("status", "created"), ("status", "shipped")]
    assert ("status", _utc("2024-01-01T03:00:00Z"), "approved") in o1.history
    assert (stats.object_attribute_values, stats.object_attribute_values_without_time) == (7, 1)

    xml = StringIO()
    formats.write_ocel_xml([{"object_id": "o1", "object_type": "order", "attributes": o1.attributes,
                             "history": o1.history}], [], xml)
    again = [r for r in formats.read_ocel_xml(BytesIO(xml.getvalue().encode())) if isinstance(r, formats.ObjectRecord)][0]
    assert again.attributes == o1.attributes and sorted(again.history) == sorted(o1.history)


def test_per_object_value_limit_is_counted(monkeypatch):
    monkeypatch.setattr(formats, "MAX_OBJECT_ATTRIBUTE_VALUES", 1)
    stats = formats.ReadStats()
    o1 = next(r for r in formats.read_ocel_json(BytesIO(json.dumps(_orders()).encode()), stats=stats)
              if isinstance(r, formats.ObjectRecord))
    # o1's stored-value entries (status created, region EU) are exempt; one of its two other values fits.
    assert stats.object_attribute_values_over_limit == 1
    assert sorted(v for _, _, v in o1.history) == ["EU", "created", "shipped"]


def test_changes_and_stored_value_comparison_match_hand_counts():
    made = _import(_orders())
    assert made["object_attribute_values"] == {"read": 7, "without_timezone_qualified_time": 1,
                                               "over_per_object_limit": 0, "per_object_limit": 1000,
                                               "per_object_limit_excludes_stored_values": True}
    result = attributes.attribute_changes(made["log_id"])
    assert result["stored_value_comparison_complete"] and result["execution_authorized"] is False
    status = _status(result)
    assert (status["objects_with_values"], status["timed_values"]) == (3, 5)  # o3's two values share one time
    assert status["initial_values_without_known_time"] == 1 and status["times_with_several_values"] == 1
    assert (status["objects_whose_value_changes"], status["value_changes"]) == (1, 2)
    assert status["most_frequent_changes"] == [{"from": "approved", "to": "shipped", "changes": 1},
                                               {"from": "created", "to": "approved", "changes": 1}]
    assert status["first_change_or_value_at"] == "2024-01-01T01:00:00Z"
    assert status["last_change_or_value_at"] == "2024-01-01T05:00:00Z"
    # o1: 01:00 created (same), 03:00 approved (applies at its own time), 06:00 shipped; o2 before; o3 ambiguous.
    assert status["event_links"] == {"examined": 5, "before_first_value": 1, "at_a_time_with_several_values": 1,
                                     "where_stored_value_differs_from_value_then": 2}
    region = {a["name"]: a for a in result["object_types"][0]["attributes"]}["region"]
    assert region["value_changes"] == 0 and region["event_links"]["where_stored_value_differs_from_value_then"] == 0

    timeline = attributes.attribute_changes(made["log_id"], object_id="o1")["object"]
    assert timeline["linked_events"] == 3 and [v["value"] for v in timeline["values"]] == \
        ["EU", "created", "approved", "shipped"]
    with pytest.raises(workspace.EventLogWorkspaceError):
        attributes.attribute_changes(made["log_id"], object_id="missing")
    assert attributes.attribute_changes(made["log_id"], object_type="item")["object_types"] == []


def test_comparison_stops_at_its_work_budget(monkeypatch):
    monkeypatch.setattr(attributes, "MAX_ASOF_LINKS", 2)
    result = attributes.attribute_changes(_import(_orders())["log_id"])
    assert not result["stored_value_comparison_complete"]
    assert result["stored_value_comparison_unknown_reason"] == "event_link_work_budget"
    status = _status(result)
    assert "event_links" not in status and status["value_changes"] == 2  # change counts stay exact


@pytest.mark.parametrize("fmt", ["ocel", "ocelxml"])
def test_exports_round_trip_the_history_and_the_stored_value(fmt):
    log = _import(_orders())["log_id"]
    out = StringIO()
    workspace.export_log(log, fmt, out)
    again = workspace.import_log(BytesIO(out.getvalue().encode()), name="again", source_format=fmt)
    first, second = attributes.attribute_changes(log), attributes.attribute_changes(again["log_id"])
    assert first["object_types"] == second["object_types"]
    conn = workspace._connect()
    try:
        stored = [conn.execute("SELECT attributes FROM objects WHERE log_id = ? AND object_id = 'o1'", [i]).fetchone()[0]
                  for i in (log, again["log_id"])]
    finally:
        conn.close()
    assert json.loads(stored[0]) == json.loads(stored[1]) == {"status": "created", "region": "EU"}


def test_untimed_attributes_still_export_at_the_initial_time():
    out = StringIO()
    formats.write_ocel_json([{"object_id": "o", "object_type": "t", "attributes": {"a": "1"}}], [], out)
    assert json.loads(out.getvalue())["objects"][0]["attributes"] == [{"name": "a", "time": T0, "value": "1"}]


def test_append_adds_later_values_for_existing_objects_once():
    log = _import(_orders())["log_id"]
    newer = _orders()
    newer["objects"][0]["attributes"].append(_value("status", "2024-01-02T00:00:00Z", "delivered"))
    newer["events"] = [{"id": "e5", "type": "Deliver", "time": "2024-01-02T01:00:00Z",
                        "relationships": [{"objectId": "o1", "qualifier": ""}]}]
    appended = workspace.append_log(log, BytesIO(json.dumps(newer).encode()), source_format="ocel")
    assert appended["object_attribute_values"]["added"] == 1 and appended["existing_object_attributes_kept"]
    status = _status(attributes.attribute_changes(log))
    assert status["value_changes"] == 3 and status["event_links"]["where_stored_value_differs_from_value_then"] == 3
    again = workspace.append_log(log, BytesIO(json.dumps(newer).encode()), source_format="ocel")
    assert again["object_attribute_values"]["added"] == 0


def test_flat_formats_have_no_history_and_delete_removes_it():
    csv_log = workspace.import_log(BytesIO(b"case,activity,time\n1,A,2024-01-01T00:00:00Z\n"), name="c", source_format="csv",
                                   mapping={"case_column": "case", "activity_column": "activity", "timestamp_column": "time"})
    assert "object_attribute_values" not in csv_log
    assert attributes.attribute_changes(csv_log["log_id"])["object_types"] == []
    log = _import(_orders())["log_id"]
    workspace.delete_log(log)
    conn = workspace._connect()
    try:
        assert conn.execute("SELECT count(*) FROM object_attribute_values WHERE log_id = ?", [log]).fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM object_attribute_values WHERE log_id LIKE 'stg_%'").fetchone()[0] == 0
    finally:
        conn.close()


def test_real_http_route_requires_a_dashboard_session(tmp_path):
    code = r'''
import json
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
doc = {"objectTypes": [{"name": "order", "attributes": []}], "eventTypes": [],
       "objects": [{"id": "o1", "type": "order", "attributes": [
           {"name": "status", "time": "1970-01-01T00:00:00Z", "value": "open"},
           {"name": "status", "time": "2024-01-01T09:00:00Z", "value": "closed"}]}],
       "events": [{"id": "e1", "type": "Close", "time": "2024-01-01T10:00:00Z",
                   "relationships": [{"objectId": "o1", "qualifier": ""}]}]}
with TestClient(app) as client:
    headers = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    made = client.post("/v1/event-logs/import?format=ocel&name=o", content=json.dumps(doc).encode(),
                       headers={**headers, "x-owg-local-storage-confirmed": "true"})
    path = "/v1/event-logs/" + made.json()["log_id"] + "/object-attributes"
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    assert client.get(path, headers=bearer).status_code in (401, 403)
    result = client.get(path + "?object_id=o1", headers=headers)
    assert result.status_code == 200 and result.headers["cache-control"] == "no-store", result.text
    status = result.json()["object_types"][0]["attributes"][0]
    assert status["event_links"]["where_stored_value_differs_from_value_then"] == 1
    assert len(result.json()["object"]["values"]) == 2
    assert client.get(path + "?object_id=nope", headers=headers).status_code == 422
    assert client.get("/v1/event-logs/log_0000000000000000/object-attributes", headers=headers).status_code in (404, 422)
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"


def _untimed_first(first):
    status = [{"name": "status", "value": "created"} if first is None else _value("status", first, "created"),
              _value("status", "2024-01-02T00:00:00Z", "approved")]
    return {"objectTypes": [], "eventTypes": [], "objects": [{"id": "o", "type": "order", "attributes": status}],
            "events": [{"id": "s", "type": "Ship", "time": "2024-01-03T00:00:00Z",
                        "relationships": [{"objectId": "o", "qualifier": ""}]}]}


def _stored(log_id):
    conn = workspace._connect()
    try:
        return json.loads(conn.execute("SELECT attributes FROM objects WHERE log_id = ? AND object_id = 'o'",
                                       [log_id]).fetchone()[0])
    finally:
        conn.close()


@pytest.mark.parametrize("fmt", ["ocel", "ocelxml"])
@pytest.mark.parametrize("first", [None, "not a time", "2024-01-01T00:00:00"])
def test_a_stored_value_without_a_usable_time_survives_export(fmt, first):
    log = _import(_untimed_first(first))["log_id"]
    assert _stored(log) == {"status": "created"}
    out = StringIO()
    workspace.export_log(log, fmt, out)
    again = workspace.import_log(BytesIO(out.getvalue().encode()), name="again", source_format=fmt)["log_id"]
    assert _stored(again) == {"status": "created"}
    # The stored value goes out at the documented unknown-time origin and is reported as such, not as a change time.
    status = _status(attributes.attribute_changes(again))
    assert status["initial_values_without_known_time"] == 1 and status["first_change_or_value_at"] == "2024-01-02T00:00:00Z"


def test_append_keeping_a_static_value_still_exports_it():
    base = _untimed_first("2024-01-01T00:00:00Z")
    base["objects"][0]["attributes"] = [_value("region", T0, "EU")]
    log = _import(base)["log_id"]
    conn = workspace._connect()
    try:  # a log imported before timed values were kept has the stored value only
        conn.execute("DELETE FROM object_attribute_values WHERE log_id = ?", [log])
    finally:
        conn.close()
    newer = json.loads(json.dumps(base))
    newer["objects"][0]["attributes"] = [_value("region", "2024-01-02T00:00:00Z", "US"),
                                         _value("priority", "2024-01-05T00:00:00Z", "high"),
                                         _value("priority", "2024-01-04T00:00:00Z", "low")]
    workspace.append_log(log, BytesIO(json.dumps(newer).encode()), source_format="ocel")
    for fmt in ("ocel", "ocelxml"):
        out = StringIO()
        workspace.export_log(log, fmt, out)
        again = workspace.import_log(BytesIO(out.getvalue().encode()), name="again", source_format=fmt)["log_id"]
        assert _stored(log) == {"region": "EU"}
        # An attribute only an append added has no stored value; OCEL cannot say so, so a re-import stores
        # its earliest value. Existing stored values are never replaced.
        assert _stored(again) == {"region": "EU", "priority": "low"}


def _many(first, n, start="2024-01-01T00:00:00Z"):
    """One order whose status has a stored first value (``first`` time, None for none) and n later values."""
    base = _utc(start)
    status = [{"name": "status", "value": "created"} if first is None else _value("status", first, "created")]
    status += [_value("status", (base + timedelta(minutes=i)).isoformat().replace("+00:00", "Z"), f"v{i:04d}")
               for i in range(n)]
    return {"objectTypes": [], "eventTypes": [], "objects": [{"id": "o", "type": "order", "attributes": status}],
            "events": [{"id": "s", "type": "Ship", "time": "2024-02-01T00:00:00Z",
                        "relationships": [{"objectId": "o", "qualifier": ""}]}]}


def _history(log_id):
    conn = workspace._connect()
    try:
        return conn.execute("""SELECT name, valid_from, value FROM object_attribute_values WHERE log_id = ?
            ORDER BY 1, 2, 3""", [log_id]).fetchall()
    finally:
        conn.close()


def _roundtrip(log_id, fmt):
    out = StringIO()
    workspace.export_log(log_id, fmt, out)
    return workspace.import_log(BytesIO(out.getvalue().encode()), name="again", source_format=fmt)


@pytest.mark.parametrize("fmt", ["ocel", "ocelxml"])
@pytest.mark.parametrize("first", [None, "2023-12-31T00:00:00Z"])
def test_a_history_at_the_limit_survives_export(fmt, first):
    made = _import(_many(first, formats.MAX_OBJECT_ATTRIBUTE_VALUES))
    assert made["object_attribute_values"]["over_per_object_limit"] == 0
    again = _roundtrip(made["log_id"], fmt)
    assert again["object_attribute_values"]["over_per_object_limit"] == 0
    original, copied = _history(made["log_id"]), _history(again["log_id"])
    assert copied[-1][2] == original[-1][2] == "v0999" and _stored(again["log_id"]) == {"status": "created"}
    # Only the stored value gains an entry, at the unknown-time origin, when it had no time of its own.
    assert set(original) <= set(copied) and len(copied) - len(original) == (first is None)


@pytest.mark.parametrize("fmt", ["ocel", "ocelxml"])
def test_appends_keep_the_limit_and_count_what_they_leave_out(fmt):
    log = _import(_many(None, 990))["log_id"]
    later = _many(None, 20, start="2024-03-01T00:00:00Z")
    appended = workspace.append_log(log, BytesIO(json.dumps(later).encode()), source_format="ocel")
    assert (appended["object_attribute_values"]["added"],
            appended["object_attribute_values"]["over_per_object_limit"]) == (10, 10)
    assert len(_history(log)) == formats.MAX_OBJECT_ATTRIBUTE_VALUES
    again = _roundtrip(log, fmt)
    assert again["object_attribute_values"]["over_per_object_limit"] == 0
    assert set(_history(log)) <= set(_history(again["log_id"])) and _stored(again["log_id"]) == {"status": "created"}
