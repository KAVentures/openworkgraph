"""OCEL 2.0 XML import and export for the event-log workspace (roadmap #326 B)."""
from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO, StringIO
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from xml.etree import ElementTree as ET

import pytest

from server import event_log_workspace as workspace
from shared import event_log_formats as formats
from test_event_log_workspace import OCEL

ROOT = Path(__file__).resolve().parents[1]

SAMPLE = """<?xml version='1.0' encoding='UTF-8'?>
<log>
  <object-types><object-type name="order"><attributes><attribute name="region" type="string"/></attributes></object-type>
    <object-type name="item"><attributes/></object-type></object-types>
  <event-types><event-type name="place"><attributes/></event-type></event-types>
  <objects>
    <object id="o1" type="order"><attributes><attribute name="region" time="1970-01-01T00:00:00Z">EU</attribute></attributes>
      <objects><relationship object-id="i1" qualifier="contains"/></objects></object>
    <object id="i1" type="item"/>
    <object id="i2" type="item"/>
  </objects>
  <events>
    <event id="e1" type="place" time="2024-01-01T09:00:00+01:00"><attributes><attribute name="channel">web</attribute></attributes>
      <objects><relationship object-id="o1" qualifier="order"/><relationship object-id="i1" qualifier="item"/>
        <relationship object-id="ghost" qualifier="x"/></objects></event>
    <event id="e2" type="pick" time="2024-01-01T10:00:00Z"><objects><relationship object-id="i1" qualifier="item"/></objects></event>
    <event id="e3" type="ship" time="2024-01-01T12:00:00Z"><objects><relationship object-id="o1" qualifier="order"/>
      <relationship object-id="i1" qualifier="item"/></objects></event>
    <event id="e4" type="pick" time="no time"><objects><relationship object-id="i2" qualifier="item"/></objects></event>
  </events>
</log>"""


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def test_reader_follows_the_ocel_2_xml_layout():
    stats = formats.ReadStats()
    records = list(formats.read_ocel_xml(BytesIO(SAMPLE.encode()), stats=stats))
    objects = [r for r in records if isinstance(r, formats.ObjectRecord)]
    events = [r for r in records if isinstance(r, formats.EventRecord)]
    assert [(o.object_id, o.object_type) for o in objects] == [("o1", "order"), ("i1", "item"), ("i2", "item")]
    assert objects[0].attributes == {"region": "EU"}
    assert events[0].timestamp == datetime(2024, 1, 1, 8, tzinfo=timezone.utc)
    assert events[0].objects == [("o1", "order"), ("i1", "item")]  # unknown objects are not linked
    assert events[0].attributes == {"channel": "web"}
    assert (stats.objects, stats.events, stats.rejected_events) == (3, 3, 1)


def test_workspace_imports_xml_like_json_and_round_trips_both_ways():
    from_xml = workspace.import_log(BytesIO(SAMPLE.encode()), name="xml", source_format="ocelxml")
    assert (from_xml["events"], from_xml["objects"], from_xml["rejected_events"]) == (3, 3, 1)
    assert from_xml["source_format"] == "ocelxml"
    from_json = workspace.import_log(BytesIO(json.dumps(OCEL).encode()), name="json", source_format="ocel")
    for log_id in (from_xml["log_id"], from_json["log_id"]):
        exported = StringIO()
        workspace.export_log(log_id, "ocelxml", exported)
        ET.fromstring(exported.getvalue())  # well-formed
        again = workspace.import_log(BytesIO(exported.getvalue().encode()), name="again", source_format="ocelxml")
        for kind in ("order", "item"):
            assert workspace.variants(log_id, kind)["variants"] == workspace.variants(again["log_id"], kind)["variants"]
        as_json = StringIO()
        workspace.export_log(again["log_id"], "ocel", as_json)
        assert {e["id"] for e in json.loads(as_json.getvalue())["events"]} == {
            e["id"] for e in json.loads(_json_export(log_id))["events"]}


def _json_export(log_id):
    out = StringIO()
    workspace.export_log(log_id, "ocel", out)
    return out.getvalue()


def test_writer_escapes_markup_and_drops_characters_xml_cannot_hold():
    out = StringIO()
    formats.write_ocel_xml(
        [{"object_id": "o&1", "object_type": "or<der>", "attributes": {"note": "a\x01b & 'c'"}}],
        [{"event_id": "e\"1", "activity": "Place <x>", "timestamp": datetime(2024, 1, 1, tzinfo=timezone.utc),
          "attributes": {"k": "v\x0bw"}, "objects": [("o&1", "q'")]}], out)
    records = list(formats.read_ocel_xml(BytesIO(out.getvalue().encode())))
    assert records[0].object_id == "o&1" and records[0].object_type == "or<der>"
    assert records[0].attributes == {"note": "ab & 'c'"}
    assert records[1].activity == "Place <x>" and records[1].objects == [("o&1", "q'")]
    assert records[1].attributes == {"k": "vw"}


@pytest.mark.parametrize("document,message", [
    ('<?xml version="1.0"?><!DOCTYPE log [<!ENTITY x "y">]><log/>', "DOCTYPE"),
    ("<log><objects><object", "parsed"),
    ("<events/>", "log root"),
    ("<log><events/><objects><object id='o' type='t'/></objects></log>", "precede"),
    ("<log><objects><object id='o' type='t'/><object id='o' type='u'/></objects></log>", "unique"),
])
def test_unsafe_or_malformed_xml_is_refused(document, message):
    with pytest.raises(formats.EventLogFormatError, match=message):
        list(formats.read_ocel_xml(BytesIO(document.encode())))
    with pytest.raises(workspace.EventLogWorkspaceError):
        workspace.import_log(BytesIO(document.encode()), name="bad", source_format="ocelxml")
    assert workspace.list_logs()["logs"] == []


def test_duplicate_event_ids_are_refused_like_json():
    document = SAMPLE.replace('id="e2"', 'id="e1"')
    with pytest.raises(workspace.EventLogWorkspaceError):
        workspace.import_log(BytesIO(document.encode()), name="dup", source_format="ocelxml")


def test_large_documents_stream_without_holding_every_element():
    events = 200_000
    parts = ["<log><objects><object id='o' type='order'/></objects><events>"]
    parts += [f"<event id='e{i}' type='step' time='2024-01-01T00:00:00Z'><objects>"
              f"<relationship object-id='o' qualifier=''/></objects></event>" for i in range(events)]
    parts.append("</events></log>")
    started = time.perf_counter()
    count = sum(1 for r in formats.read_ocel_xml(BytesIO("".join(parts).encode())) if isinstance(r, formats.EventRecord))
    assert count == events and time.perf_counter() - started < 30


def test_real_http_xml_import_and_export(tmp_path):
    code = r'''
from xml.etree import ElementTree as ET
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth

doc = b"""<log><objects><object id='o' type='order'/></objects><events>
<event id='a' type='Place' time='2024-01-01T00:00:00Z'><objects><relationship object-id='o' qualifier=''/></objects></event>
</events></log>"""
with TestClient(app) as client:
    headers = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    made = client.post("/v1/event-logs/import?format=ocelxml&name=x", content=doc,
                       headers={**headers, "x-owg-local-storage-confirmed": "true"})
    assert made.status_code == 200 and made.json()["source_format"] == "ocelxml", made.text
    exported = client.get("/v1/event-logs/" + made.json()["log_id"] + "/export?format=ocelxml", headers=headers)
    assert exported.status_code == 200 and exported.headers["content-type"].startswith("application/xml")
    assert ".xmlocel" in exported.headers["content-disposition"]
    assert ET.fromstring(exported.content).find("events/event").get("type") == "Place"
'''
    env = os.environ.copy()
    env.update({
        "WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"),
        "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
        "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json"),
    })
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
