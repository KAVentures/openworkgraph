"""XES and OCEL files whose times have no UTC offset (as PM4Py writes naive times) under an explicit decision."""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from io import BytesIO

import pytest

from server import event_log_quality as quality
from server import event_log_workspace as workspace
from server.event_log_workspace import EventLogWorkspaceError

XES = """<?xml version="1.0" encoding="UTF-8"?>
<log xes.version="1.0" xmlns="http://www.xes-standard.org/">
 <trace><string key="concept:name" value="c1"/>
  <event><string key="concept:name" value="A"/><date key="time:timestamp" value="2024-03-04T14:05:00"/></event>
  <event><string key="concept:name" value="B"/><date key="time:timestamp" value="2024-03-04T15:00:00.250"/></event>
  <event><string key="concept:name" value="C"/><date key="time:timestamp" value="2024-03-04T16:00:00Z"/></event>
  <event><string key="concept:name" value="D"/><date key="time:timestamp" value="04/03/2024 17:00"/></event>
 </trace></log>"""


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _times(log_id, case_type=None):
    return [(e["activity"], e["timestamp"].isoformat()) for _, evs in workspace.case_events(log_id, case_type) for e in evs]


def test_xes_without_offsets_needs_a_decision_and_then_imports():
    with pytest.raises(EventLogWorkspaceError, match="times have no UTC offset; choose the offset"):
        workspace.import_log(BytesIO(XES.replace("2024-03-04T16:00:00Z", "2024-03-04T16:00:00").encode()),
                             name="x", source_format="xes")
    result = workspace.import_log(BytesIO(XES.encode()), name="x", source_format="xes",
                                  mapping={"naive_timestamp_offset": "+01:00"})
    # Unzoned times read at +01:00; zoned ones as written; a non-ISO value is still rejected.
    assert _times(result["log_id"]) == [("A", "2024-03-04T13:05:00+00:00"), ("B", "2024-03-04T14:00:00.250000+00:00"),
                                        ("C", "2024-03-04T16:00:00+00:00")]
    assert result["timestamps_with_assumed_offset"] == 2 and result["rejected_events"] == 1
    transform = quality.quality(result["log_id"])["reports"][0]["transform"]
    assert transform["mapping"]["naive_timestamp_offset"] == "+01:00"
    plain = workspace.import_log(BytesIO(XES.encode()), name="y", source_format="xes")
    assert plain["events"] == 1 and plain["timestamps_without_offset"] == 2
    assert "naive_timestamp_offset" not in quality.quality(plain["log_id"])["reports"][0]["transform"]["mapping"]


def test_ocel_json_and_xml_event_and_attribute_times():
    ocel = {"objectTypes": [{"name": "order", "attributes": [{"name": "status", "type": "string"}]}],
            "eventTypes": [{"name": "Open", "attributes": []}],
            "objects": [{"id": "o1", "type": "order",
                         "attributes": [{"name": "status", "time": "2024-03-04T14:00:00", "value": "new"}]}],
            "events": [{"id": "e1", "type": "Open", "time": "2024-03-04T14:05:00",
                        "relationships": [{"objectId": "o1", "qualifier": ""}]}]}
    result = workspace.import_log(BytesIO(json.dumps(ocel).encode()), name="o", source_format="ocel",
                                  mapping={"naive_timestamp_offset": "-05:00"})
    assert _times(result["log_id"], "order") == [("Open", "2024-03-04T19:05:00+00:00")]
    assert result["timestamps_with_assumed_offset"] == 2
    assert result["object_attribute_values"]["without_timezone_qualified_time"] == 0
    text = io.StringIO()
    workspace.export_log(result["log_id"], "ocelxml", text)
    naive_xml = text.getvalue().replace("2024-03-04T19:05:00Z", "2024-03-04T19:05:00")
    again = workspace.import_log(BytesIO(naive_xml.encode()), name="ox", source_format="ocelxml",
                                 mapping={"naive_timestamp_offset": "Z"})
    assert _times(again["log_id"], "order") == [("Open", "2024-03-04T19:05:00+00:00")]


def test_offset_is_validated_and_csv_only_options_stay_csv_only():
    with pytest.raises(EventLogWorkspaceError, match="must look like"):
        workspace.import_log(BytesIO(XES.encode()), name="x", source_format="xes", mapping={"naive_timestamp_offset": "1h"})
    with pytest.raises(EventLogWorkspaceError, match="CSV only"):
        workspace.import_log(BytesIO(XES.encode()), name="x", source_format="xes", mapping={"timestamp_format": "%d/%m/%Y"})


def test_append_and_route(tmp_path):
    log = workspace.import_log(BytesIO(XES.encode()), name="x", source_format="xes",
                               mapping={"naive_timestamp_offset": "Z"})["log_id"]
    newer = XES.replace('value="c1"', 'value="c2"')
    workspace.append_log(log, BytesIO(newer.encode()), source_format="xes", mapping={"naive_timestamp_offset": "Z"})
    assert len(_times(log)) == 6
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
data = open(r"%s", "rb").read()
with TestClient(app) as client:
    owner = {'Authorization': 'OWG-Session ' + local_auth.create_dashboard_session(), 'X-OWG-Local-Storage-Confirmed': 'true'}
    r = client.post('/v1/event-logs/import?format=xes&name=a', content=data, headers=owner)
    assert r.status_code == 422 and 'no UTC offset' in r.text, r.text
    r = client.post('/v1/event-logs/import?format=xes&name=a&naive_timestamp_offset=%%2B02:00', content=data, headers=owner)
    assert r.status_code == 200 and r.json()['events'] == 3, r.text
'''
    path = tmp_path / "naive.xes"
    path.write_text(XES.replace("2024-03-04T16:00:00Z", "2024-03-04T16:00:00"))
    env = {**os.environ, 'WORKFLOW_OBSERVER_DATA': str(tmp_path / 'data'), 'WORKFLOW_OBSERVER_AUTH_DIR': str(tmp_path / 'auth')}
    subprocess.run([sys.executable, '-c', code % path], env=env, check=True, timeout=60)
