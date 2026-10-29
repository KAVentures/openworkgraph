"""Event-log workspace: CSV/XES/OCEL 2.0 import/export, columnar queries and local auth."""
from __future__ import annotations

from io import BytesIO, StringIO
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import event_log_workspace as workspace
from server import event_log_quality as quality
from shared import event_log_formats as formats

ROOT = Path(__file__).resolve().parents[1]
MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time",
           "resource_column": "who"}

CSV = (
    "case,activity,time,who\n"
    "c1,Receive,2024-01-01T09:00:00Z,ann\n"
    "c1,Review,2024-01-01T10:00:00+01:00,bob\n"  # 09:00Z, tie broken by event id
    "c1,Close,2024-01-01T11:00:00Z,\n"
    "c2,Receive,2024-01-02T09:00:00Z,ann\n"
    "c2,Close,2024-01-02T12:00:00Z,ann\n"
    "c3,Receive,2024-01-03 09:00:00,ann\n"  # no timezone: rejected, never guessed
)

OCEL = {
    "objectTypes": [{"name": "order", "attributes": []}, {"name": "item", "attributes": []}],
    "eventTypes": [{"name": "place", "attributes": []}],
    "objects": [
        {"id": "o1", "type": "order", "attributes": [{"name": "region", "value": "EU", "time": "2024-01-01T00:00:00Z"}]},
        {"id": "i1", "type": "item"}, {"id": "i2", "type": "item"},
    ],
    "events": [
        {"id": "e1", "type": "place", "time": "2024-01-01T09:00:00Z",
         "relationships": [{"objectId": "o1", "qualifier": "order"}, {"objectId": "i1", "qualifier": "item"},
                           {"objectId": "i2", "qualifier": "item"}]},
        {"id": "e2", "type": "pick", "time": "2024-01-01T10:00:00Z",
         "relationships": [{"objectId": "i1", "qualifier": "item"}, {"objectId": "ghost", "qualifier": "x"}]},
        {"id": "e3", "type": "ship", "time": "2024-01-01T12:00:00Z",
         "relationships": [{"objectId": "o1", "qualifier": "order"}, {"objectId": "i1", "qualifier": "item"}]},
        {"id": "e4", "type": "pick", "time": "not a time", "relationships": [{"objectId": "i2", "qualifier": "item"}]},
    ],
}


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)
    yield tmp_path


def _import(text: str | bytes, fmt: str, **kwargs):
    data = text.encode() if isinstance(text, str) else text
    return workspace.import_log(BytesIO(data), name=kwargs.pop("name", "log"), source_format=fmt, **kwargs)


def test_csv_import_rejects_naive_times_and_mines_variants():
    log = _import(CSV, "csv", mapping=MAPPING)
    assert (log["events"], log["rejected_events"], log["objects"]) == (5, 1, 2)
    result = workspace.variants(log["log_id"])
    assert result["cases"] == 2 and result["distinct_variants"] == 2
    assert {tuple(v["path"]) for v in result["variants"]} == {("Receive", "Review", "Close"), ("Receive", "Close")}
    assert result["causal_claims"] is False and result["case_notion"] == "case"
    summary = workspace.summary(log["log_id"])
    assert summary["cases"] == 2 and summary["events"] == 5
    flows = {(f["from"], f["to"]): f["cases"] for f in workspace.directly_follows(log["log_id"])["edges"]}
    assert flows[("Receive", "Close")] == 1 and flows[("Review", "Close")] == 1
    activities = {a["activity"]: a for a in workspace.activities(log["log_id"])["activities"]}
    assert activities["Receive"]["cases"] == 2


def test_bulk_csv_blank_resources_match_streaming_reader_and_quality_counts():
    """Whitespace-only resources are missing; padded nonblank IDs stay literal."""
    text = (
        "case,activity,time,who\n"
        "c1,Open,2024-01-01T09:00:00Z,   \n"
        "c1,Review,2024-01-01T10:00:00Z,\n"
        "c1,Approve,2024-01-01T11:00:00Z, worker \n"
        "c1,Close,2024-01-01T12:00:00Z,worker\n"
    )
    expected = [row.resource for row in formats.read_csv(
        StringIO(text), case_column="case", activity_column="activity",
        timestamp_column="time", resource_column="who")
        if isinstance(row, formats.EventRecord)]
    assert expected == ["", "", " worker ", "worker"]
    log = _import(text, "csv", mapping=MAPPING)
    assert log["events"] == 4 and log["rejected_events"] == 0
    assert quality.quality(log["log_id"])["reports"][0]["diagnostics"]["events_without_resource"] == 2
    conn = workspace._connect()
    try:
        stored = [row[0] for row in conn.execute(
            "SELECT resource FROM events WHERE log_id = ? ORDER BY event_id",
            [log["log_id"]]).fetchall()]
    finally:
        conn.close()
    assert stored == expected

    # Normalizing at INSERT must not weaken raw control-character rejection.
    # A tab inside a quoted resource is still rejected before storage.
    with pytest.raises(workspace.EventLogWorkspaceError, match="control characters"):
        _import('case,activity,time,who\n'
                'c1,Open,2024-01-01T09:00:00Z," \t "\n', "csv", mapping=MAPPING)


def test_csv_missing_columns_and_bad_mapping_are_safe_errors():
    with pytest.raises(workspace.EventLogWorkspaceError):
        _import(CSV, "csv", mapping={**MAPPING, "case_column": "nope"})
    with pytest.raises(workspace.EventLogWorkspaceError):
        _import("case,activity,time\nc1,A,yesterday\n", "csv", mapping=MAPPING | {"resource_column": ""})
    with pytest.raises(workspace.EventLogWorkspaceError):
        _import(CSV, "parquet", mapping=MAPPING)
    with pytest.raises(workspace.EventLogWorkspaceError):
        _import(CSV, "csv", mapping=MAPPING, name="<script>")
    assert workspace.list_logs()["logs"] == []  # failed imports leave nothing behind


def test_xes_round_trip_preserves_cases_order_and_resources():
    log = _import(CSV, "csv", mapping=MAPPING)
    out = StringIO()
    workspace.export_log(log["log_id"], "xes", out)
    xes = out.getvalue()
    assert "<log " in xes and 'key="concept:name"' in xes and 'key="org:resource" value="bob"' in xes
    again = _import(xes, "xes", name="from xes")
    assert again["events"] == 5 and again["rejected_events"] == 0
    first = {tuple(v["path"]): v["cases"] for v in workspace.variants(log["log_id"])["variants"]}
    second = {tuple(v["path"]): v["cases"] for v in workspace.variants(again["log_id"])["variants"]}
    assert first == second


def test_xes_with_doctype_or_entities_is_refused_before_parsing():
    evil = ('<?xml version="1.0"?><!DOCTYPE log [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
            '<log><trace><string key="concept:name" value="&x;"/></trace></log>')
    with pytest.raises(workspace.EventLogWorkspaceError, match="DOCTYPE"):
        _import(evil, "xes")
    with pytest.raises(workspace.EventLogWorkspaceError):
        _import("<log><trace>", "xes")


def test_ocel_import_is_object_centric_and_flattens_per_object_type():
    log = _import(json.dumps(OCEL), "ocel")
    assert log["events"] == 3 and log["rejected_events"] == 1 and log["objects"] == 3
    overview = workspace.object_types(log["log_id"])
    types = {t["object_type"]: t for t in overview["object_types"]}
    assert types["item"]["objects"] == 2 and types["order"]["objects"] == 1
    assert overview["shared_events_between_types"] == [{"types": ["item", "order"], "events": 2}]
    orders = {tuple(v["path"]) for v in workspace.variants(log["log_id"], "order")["variants"]}
    items = {tuple(v["path"]): v["cases"] for v in workspace.variants(log["log_id"], "item")["variants"]}
    assert orders == {("place", "ship")}
    assert items == {("place", "pick", "ship"): 1, ("place",): 1}  # e4 had no usable time
    with pytest.raises(workspace.EventLogWorkspaceError):
        workspace.variants(log["log_id"], "no-such-type")


def test_ocel_export_reimports_to_the_same_object_graph():
    log = _import(json.dumps(OCEL), "ocel")
    out = StringIO()
    workspace.export_log(log["log_id"], "ocel", out)
    exported = json.loads(out.getvalue())
    assert {"objectTypes", "eventTypes", "objects", "events"} <= set(exported)
    again = _import(out.getvalue(), "ocel", name="again")
    assert (again["events"], again["objects"]) == (3, 3)
    for kind in ("order", "item"):
        assert workspace.variants(log["log_id"], kind)["variants"] == workspace.variants(again["log_id"], kind)["variants"]


def test_csv_export_matches_events():
    log = _import(CSV, "csv", mapping=MAPPING)
    out = StringIO()
    workspace.export_log(log["log_id"], "csv", out)
    lines = out.getvalue().strip().splitlines()
    assert len(lines) == 6 and lines[0].startswith("case_id,activity,timestamp")


def test_malformed_ocel_is_rejected():
    for body in ("{", json.dumps({"events": []}), json.dumps({"objects": "x", "events": []})):
        with pytest.raises(workspace.EventLogWorkspaceError):
            _import(body, "ocel")


def test_delete_removes_every_row_and_unknown_ids_are_refused():
    log = _import(CSV, "csv", mapping=MAPPING)
    other = _import(json.dumps(OCEL), "ocel", name="other")
    workspace.delete_log(log["log_id"])
    assert [entry["log_id"] for entry in workspace.list_logs()["logs"]] == [other["log_id"]]
    with pytest.raises(workspace.EventLogWorkspaceError):
        workspace.summary(log["log_id"])
    for bad in ("log_../../x", "log_123", "'; DROP TABLE logs; --"):
        with pytest.raises(workspace.EventLogWorkspaceError):
            workspace.summary(bad)
    import duckdb
    conn = duckdb.connect(str(workspace.database_path()))
    try:
        for table in ("events", "objects", "event_objects"):
            assert conn.execute(f"SELECT count(*) FROM {table} WHERE log_id = ?", [log["log_id"]]).fetchone()[0] == 0
    finally:
        conn.close()


def test_log_limit_is_enforced(monkeypatch):
    monkeypatch.setattr(workspace, "MAX_LOGS", 1)
    _import(CSV, "csv", mapping=MAPPING)
    with pytest.raises(workspace.EventLogWorkspaceError, match="delete"):
        _import(CSV, "csv", mapping=MAPPING)


def test_database_is_private_to_the_user(_data_dir):
    _import(CSV, "csv", mapping=MAPPING)
    if os.name != "nt":
        assert workspace.database_path().stat().st_mode & 0o077 == 0
    assert not [p for p in _data_dir.iterdir() if p.name.startswith("owg-eventlog-")]  # temp staging removed


def test_format_helpers_require_timezones_and_bound_values():
    assert formats.parse_timestamp("2024-01-01T10:00:00+02:00").isoformat() == "2024-01-01T08:00:00+00:00"
    assert formats.parse_timestamp("2024-01-01T10:00:00") is None
    assert formats.parse_timestamp("garbage") is None
    assert formats.detect_format(b'{"objects": []}') == "ocel"
    assert formats.detect_format(b"<?xml version='1.0'?><log>") == "xes"
    assert formats.detect_format(b"case,activity,time\n") == "csv"


def test_real_http_routes_need_a_dashboard_session_and_confirmation(tmp_path):
    code = r'''
import json
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth

csv_body = b"case,activity,time\nc1,A,2024-01-01T00:00:00Z\nc1,B,2024-01-01T01:00:00Z\n"
query = "format=csv&name=t&case_column=case&activity_column=activity&timestamp_column=time"
with TestClient(app) as client:
    assert client.get("/v1/event-logs").status_code == 401
    assert client.get("/v1/event-logs", headers={"Authorization": "OWG-Session wrong"}).status_code in (401, 403)
    headers = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    listed = client.get("/v1/event-logs", headers=headers)
    assert listed.status_code == 200 and listed.json()["logs"] == [], listed.text
    assert listed.headers["cache-control"] == "no-store"
    refused = client.post("/v1/event-logs/import?" + query, content=csv_body, headers=headers)
    assert refused.status_code == 403
    confirmed = {**headers, "x-owg-local-storage-confirmed": "true"}
    made = client.post("/v1/event-logs/import?" + query, content=csv_body, headers=confirmed)
    assert made.status_code == 200, made.text
    log_id = made.json()["log_id"]
    assert client.get(f"/v1/event-logs/{log_id}/variants", headers=headers).json()["variants"][0]["path"] == ["A", "B"]
    assert client.get(f"/v1/event-logs/{log_id}/directly-follows", headers=headers).status_code == 200
    assert client.get(f"/v1/event-logs/{log_id}/object-types", headers=headers).status_code == 200
    exported = client.get(f"/v1/event-logs/{log_id}/export?format=ocel", headers=headers)
    assert exported.status_code == 200 and json.loads(exported.content)["events"]
    assert exported.headers["cache-control"] == "no-store"
    assert client.get(f"/v1/event-logs/{log_id}/export?format=pdf", headers=headers).status_code == 422
    assert client.get("/v1/event-logs/log_0000000000000000/summary", headers=headers).status_code == 422
    bad = client.post("/v1/event-logs/import?format=xes&name=x", content=b"<!DOCTYPE x><log/>", headers=confirmed)
    assert bad.status_code == 422
    invalid_body=("case,activity,time\nPRIVATE_"+"X"*200+",A,2026-10-20T00:00:00Z\n").encode()
    invalid=client.post("/v1/event-logs/import?"+query,content=invalid_body,headers=confirmed)
    assert invalid.status_code==422 and "PRIVATE_" not in invalid.text
    assert len(client.get("/v1/event-logs",headers=headers).json()["logs"])==1
    assert client.get("/v1/event-logs",headers={"Authorization":"Bearer "+local_auth.ensure_api_token()}).status_code==403
    qualified={"objects":[{"id":"o","type":"order"}],"events":[{"id":"e","type":"Review","time":"2026-10-20T00:00:00Z",
        "relationships":[{"objectId":"o","qualifier":"created"},{"objectId":"o","qualifier":"reviewed"}]}]}
    made_qualified=client.post("/v1/event-logs/import?format=ocel&name=qualified",content=json.dumps(qualified).encode(),headers=confirmed)
    assert made_qualified.status_code==200,made_qualified.text
    qualified_id=made_qualified.json()["log_id"]
    assert client.get(f"/v1/event-logs/{qualified_id}/summary",headers=headers).json()["events"]==1
    exported_qualified=client.get(f"/v1/event-logs/{qualified_id}/export?format=ocel",headers=headers).json()
    assert len(exported_qualified["events"][0]["relationships"])==2
    assert client.post(f"/v1/event-logs/{qualified_id}/delete",headers=headers).status_code==200
    assert client.post(f"/v1/event-logs/{log_id}/delete", headers=headers).status_code == 200
    assert client.get("/v1/event-logs", headers=headers).json()["logs"] == []
'''
    env = os.environ.copy()
    env.update({
        "WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"),
        "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
        "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json"),
    })
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"


def test_case_keys_and_activity_labels_are_literal_in_bulk_and_streaming_csv():
    import csv
    identifiers=['Case',' Case','Case ','case','e\u0301','é']
    labels=['Review',' Review','Review ','review','Check','Close']
    stream=StringIO(newline='');writer=csv.writer(stream);writer.writerow(['case','activity','time','who'])
    for index,(identifier,label) in enumerate(zip(identifiers,labels)):
        writer.writerow([identifier,label,f'2026-10-20T00:0{index}:00Z',' person '])
    text=stream.getvalue()
    records=list(formats.read_csv(StringIO(text),**MAPPING))
    assert [row.object_id for row in records if isinstance(row,formats.ObjectRecord)]==identifiers
    assert [row.activity for row in records if isinstance(row,formats.EventRecord)]==labels
    log=_import(text,'csv',mapping=MAPPING)
    assert workspace.summary(log['log_id'])['cases']==6
    assert {tuple(row['path']) for row in workspace.variants(log['log_id'])['variants']}=={(label,) for label in labels}
    exported=StringIO();workspace.export_log(log['log_id'],'csv',exported)
    rows=list(csv.DictReader(StringIO(exported.getvalue())))
    assert {row['case_id'] for row in rows}==set(identifiers)
    assert {row['resource'] for row in rows}=={' person '}


@pytest.mark.parametrize('bad',['PRIVATE_'+('X'*200),'PRIVATE_N\x00UL','PRIVATE_\x85ID','PRIVATE_\ufffeID'])
def test_invalid_or_overlong_identifiers_never_become_prefix_keys_in_either_csv_reader(bad):
    import csv
    for field in range(3):
        values=['case','Review','person'];values[field]=bad
        stream=StringIO(newline='');writer=csv.writer(stream);writer.writerow(['case','activity','who','time'])
        writer.writerow([*values,'2026-10-20T00:00:00Z']);text=stream.getvalue()
        with pytest.raises(formats.EventLogFormatError) as exc:list(formats.read_csv(StringIO(text),**MAPPING))
        assert 'PRIVATE_' not in str(exc.value)
        with pytest.raises(workspace.EventLogWorkspaceError) as exc:_import(text,'csv',mapping=MAPPING)
        assert 'PRIVATE_' not in str(exc.value)
        assert workspace.list_logs()['logs']==[]


def test_duplicate_ocel_ids_and_overlong_foreign_references_reject_atomically():
    bad=[]
    source=json.loads(json.dumps(OCEL));source['objects'].append(dict(source['objects'][0]));bad.append(source)
    source=json.loads(json.dumps(OCEL));source['events'].append(dict(source['events'][0]));bad.append(source)
    source=json.loads(json.dumps(OCEL));source['events'][0]['relationships'][0]['objectId']='PRIVATE_'+('X'*200);bad.append(source)
    source=json.loads(json.dumps(OCEL));source['objects'][0]['id']=7;bad.append(source)
    for source in bad:
        with pytest.raises(workspace.EventLogWorkspaceError) as exc:_import(json.dumps(source),'ocel')
        assert 'PRIVATE_' not in str(exc.value)
        assert workspace.list_logs()['logs']==[]


def _identity_xes(traces):
    from xml.sax.saxutils import quoteattr
    parts=['<log>']
    for name,event_id in traces:
        parts.append('<trace>')
        if name is not None:parts.append('<string key="concept:name" value='+quoteattr(name)+'/>')
        parts.append('<event><string key="concept:name" value="Review"/><date key="time:timestamp" value="2026-10-20T00:00:00Z"/>')
        if event_id is not None:parts.append('<id key="identity:id" value='+quoteattr(event_id)+'/>')
        parts.append('</event></trace>')
    return ''.join(parts+['</log>'])


def test_xes_duplicate_and_generated_identifier_collisions_do_not_join_histories():
    for traces in ([('case',None),('case',None)],[(None,None),('trace1',None)],
        [('first',None),('second','e1')],[('first','native'),('second','native')], [('PRIVATE_'+('X'*200),None)]):
        with pytest.raises(workspace.EventLogWorkspaceError) as exc:_import(_identity_xes(traces),'xes')
        assert 'PRIVATE_' not in str(exc.value)
        assert workspace.list_logs()['logs']==[]
    log=_import(_identity_xes([('case','native'),(' case',' native'),(None,None)]),'xes')
    assert log['events']==log['objects']==3
    assert workspace.summary(log['log_id'])['cases']==3


def test_qualified_relationships_preserve_provenance_without_multiplying_case_events():
    source={'objects':[{'id':'o1','type':'order'},{'id':'o2','type':'order'}],
        'events':[{'id':'e1','type':'Review','time':'2026-10-20T00:00:00Z',
            'relationships':[{'objectId':'o1','qualifier':'created'},{'objectId':'o1','qualifier':'reviewed'},
                             {'objectId':'o2','qualifier':'observed'}]},
                  {'id':'e2','type':'Review','time':'2026-10-20T00:00:00Z',
                   'relationships':[{'objectId':'o1','qualifier':'reviewed'}]}]}
    log=_import(json.dumps(source),'ocel')
    summary=workspace.summary(log['log_id'])
    assert log['events']==2 and summary['events']==3 and summary['cases']==2
    assert {tuple(row['path']) for row in workspace.variants(log['log_id'])['variants']}=={('Review',),('Review','Review')}
    out=StringIO();workspace.export_log(log['log_id'],'ocel',out)
    events=json.loads(out.getvalue())['events'];first=next(row for row in events if row['id']=='e1')
    assert len(first['relationships'])==3  # Original qualifiers are retained for export.


@pytest.mark.parametrize('header',['case,case,activity,time','case,CASE,activity,time','case,,activity,time'])
def test_csv_duplicate_or_empty_headers_cannot_choose_different_case_columns(header):
    raw=header+'\na,b,Review,2026-10-20T00:00:00Z\n'
    with pytest.raises(formats.EventLogFormatError):list(formats.read_csv(StringIO(raw),**(MAPPING|{'resource_column':''})))
    with pytest.raises(workspace.EventLogWorkspaceError):_import(raw,'csv',mapping=MAPPING|{'resource_column':''})
    assert workspace.list_logs()['logs']==[]


def test_ocel_duplicate_fields_nonstandard_constants_and_oversized_csv_header_fail_closed():
    for raw in ('{"objects":[],"objects":[],"events":[]}',
                '{"objects":[{"id":"a","id":"b","type":"case"}],"events":[]}',
                '{"objects":[],"events":[],"other":NaN}'):
        with pytest.raises(workspace.EventLogWorkspaceError):_import(raw,'ocel')
        assert workspace.list_logs()['logs']==[]
    with pytest.raises(formats.EventLogFormatError):
        formats.read_csv_header(StringIO('H'*(formats.MAX_HEADER_CHARS+1)))


def test_literal_csv_headers_are_shared_by_streaming_and_columnar_import():
    import csv
    from shared import event_log_formats as formats
    raw=' case , activity , time , who \n Case, Review > ,2024-01-01T00:00:00Z," bot,system "\nCase ,ReviewArrow ,2024-01-01T00:00:00Z,bot \n'
    mapping={'case_column':' case ','activity_column':' activity ','timestamp_column':' time ','resource_column':' who '}
    records=list(formats.read_csv(StringIO(raw),**mapping))
    assert len([r for r in records if isinstance(r,formats.EventRecord)])==2
    log=_import(raw,'csv',mapping=mapping)
    assert workspace.summary(log['log_id'])['cases']==2
    assert {tuple(v['path']) for v in workspace.variants(log['log_id'])['variants']}=={(' Review > ',),('ReviewArrow ',)}
    out=StringIO();workspace.export_log(log['log_id'],'csv',out)
    rows=list(csv.DictReader(StringIO(out.getvalue())))
    assert {(r['case_id'],r['activity'],r['resource']) for r in rows}=={(' Case',' Review > ',' bot,system '),('Case ','ReviewArrow ','bot ')}
