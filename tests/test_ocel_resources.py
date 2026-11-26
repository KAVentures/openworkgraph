"""OCEL 2.0 resources: an event's resource travels as the ``org:resource`` attribute, as PM4Py writes it."""
from __future__ import annotations

import csv
import io
import json
from xml.etree import ElementTree as ET

import pytest

from server import event_log_workspace as workspace
from shared import event_log_formats as formats

MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time", "resource_column": "resource"}
ROWS = ('case,activity,time,resource\n'
        'c1,"Check, ""quoted""",2024-03-01T08:00:00.123456+02:00,ann\n'
        'c1,Ship,2024-03-01T09:00:00Z,\n'
        '007,Receive,2024-03-02T09:00:00Z,bob & <co>\n')


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()


def _events(log_id: str) -> list[tuple[str, ...]]:
    out = io.StringIO()
    workspace.export_log(log_id, "csv", out, None)
    return sorted(tuple(r) for r in csv.reader(io.StringIO(out.getvalue())))


def _export(log_id: str, fmt: str) -> str:
    out = io.StringIO()
    workspace.export_log(log_id, fmt, out, None)
    return out.getvalue()


@pytest.mark.parametrize("fmt", ["ocel", "ocelxml"])
def test_resources_survive_an_ocel_export_and_import(fmt):
    log = workspace.import_log(io.BytesIO(ROWS.encode()), name="t", source_format="csv", mapping=MAPPING)["log_id"]
    first = _export(log, fmt)
    again = workspace.import_log(io.BytesIO(first.encode()), name="r", source_format=fmt)["log_id"]
    assert _events(again) == _events(log)
    assert _export(again, fmt) == first   # stable: no attribute is added twice


def test_export_writes_resource_first_and_only_when_present():
    log = workspace.import_log(io.BytesIO(ROWS.encode()), name="t", source_format="csv", mapping=MAPPING)["log_id"]
    events = {e["type"]: e for e in json.loads(_export(log, "ocel"))["events"]}
    assert events["Receive"]["attributes"][0] == {"name": "org:resource", "value": "bob & <co>"}
    assert events["Ship"]["attributes"] == []
    xml = ET.fromstring(_export(log, "ocelxml"))
    names = [(a.get("name"), a.text) for e in xml.iter("event") if e.get("type") == "Receive" for a in e.iter("attribute")]
    assert names == [("org:resource", "bob & <co>")]


def _ocel_json(attributes):
    return json.dumps({"objectTypes": [{"name": "order", "attributes": []}], "eventTypes": [],
                       "objects": [{"id": "o1", "type": "order"}],
                       "events": [{"id": "e1", "type": "Pay", "time": "2024-03-01T08:00:00Z", "attributes": attributes,
                                   "relationships": [{"objectId": "o1", "qualifier": ""}]}]}).encode()


def test_pm4py_style_org_resource_becomes_the_resource_and_leaves_attributes():
    records = list(formats.read_ocel_json(io.BytesIO(_ocel_json(
        [{"name": "amount", "value": "10"}, {"name": "org:resource", "value": "ann"}]))))
    event = [r for r in records if isinstance(r, formats.EventRecord)][0]
    assert event.resource == "ann" and event.attributes == {"amount": "10"}


def test_resource_never_costs_an_attribute_slot():
    many = [{"name": f"a{i:02d}", "value": str(i)} for i in range(formats.MAX_ATTRIBUTES)]
    event = [r for r in formats.read_ocel_json(io.BytesIO(_ocel_json(many + [{"name": "org:resource", "value": "ann"}])))
             if isinstance(r, formats.EventRecord)][0]
    assert event.resource == "ann" and len(event.attributes) == formats.MAX_ATTRIBUTES


def test_an_invalid_resource_value_stays_an_attribute_instead_of_refusing_the_file():
    event = [r for r in formats.read_ocel_json(io.BytesIO(_ocel_json([{"name": "org:resource", "value": "x" * 1000}])))
             if isinstance(r, formats.EventRecord)][0]
    assert event.resource == "" and "org:resource" in event.attributes


def test_ocel_xml_reader_reads_org_resource():
    doc = ("<log><object-types/><event-types/><objects><object id='o1' type='order'/></objects><events>"
           "<event id='e1' type='Pay' time='2024-03-01T08:00:00Z'><attributes><attribute name='org:resource'>ann</attribute>"
           "<attribute name='amount'>10</attribute></attributes><objects><relationship object-id='o1' qualifier=''/></objects>"
           "</event></events></log>").encode()
    event = [r for r in formats.read_ocel_xml(io.BytesIO(doc)) if isinstance(r, formats.EventRecord)][0]
    assert event.resource == "ann" and event.attributes == {"amount": "10"}


def test_handover_analysis_works_on_an_ocel_log_with_resources():
    from server import event_log_organization as organization
    rows = "case,activity,time,resource\n" + "".join(
        f"c{i},A,2024-03-0{1 + i}T08:00:00Z,ann\nc{i},B,2024-03-0{1 + i}T09:00:00Z,bob\n" for i in range(3))
    log = workspace.import_log(io.BytesIO(rows.encode()), name="t", source_format="csv", mapping=MAPPING)["log_id"]
    again = workspace.import_log(io.BytesIO(_export(log, "ocel").encode()), name="r", source_format="ocel")["log_id"]
    result = organization.organization(again, "case", names="shown")
    assert result["handover_of_work"] == [{"from": "ann", "to": "bob", "handovers": 3, "cases": 3}]
