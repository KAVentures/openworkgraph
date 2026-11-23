"""Saving the cases that match a filter as a new event log."""
from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta, timezone
from io import BytesIO, StringIO

import pytest

from server import event_log_derive as derive
from server import event_log_workspace as workspace

START = datetime(2024, 3, 4, 8, tzinfo=timezone.utc)
MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time", "resource_column": "who",
           "attribute_columns": ["region"]}


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _csv_log():
    rows = []
    for i in range(10):
        path = ["Receive", "Escalate", "Pay"] if i % 3 == 0 else ["Receive", "Pay"]
        for k, activity in enumerate(path):
            rows.append([f"c{i}", activity, (START + timedelta(days=i, hours=k)).isoformat(), "ann",
                         "south" if i >= 6 else "north"])
    out = StringIO()
    w = csv.writer(out)
    w.writerow(["case", "activity", "time", "who", "region"])
    w.writerows(rows)
    return workspace.import_log(BytesIO(out.getvalue().encode()), name="all", source_format="csv", mapping=MAPPING)["log_id"]


def test_matching_cases_become_a_new_log_and_the_source_is_unchanged():
    log = _csv_log()
    before = workspace.summary(log)
    r = derive.derive(log, {"name": "escalated", "cohort": {"activity": "Escalate"}})
    assert r["format"] == derive.FORMAT and r["source_unchanged"] is True
    assert r["derived_from"] == {"log_id": log, "case_notion": "case", "cohort": {"activity": "Escalate"},
                                 "cases_matched": 4, "cases_in_source": 10}
    assert r["copied"] == {"events": 12, "objects": 4}
    new = r["log"]
    assert new["name"] == "escalated" and new["source_format"] == "csv" and new["events"] == 12
    assert "activity mapping" in r["not_copied"]
    summary = workspace.summary(new["log_id"])
    assert summary["cases"] == 4 and summary["events"] == 12
    # Every event of a matching case is kept, with its attributes.
    variants = workspace.variants(new["log_id"])
    assert [v["path"] for v in variants["variants"]] == [["Receive", "Escalate", "Pay"]]
    cases = dict(workspace.case_events(new["log_id"]))
    assert cases["c0"][0]["attributes"] == {"region": "north"} and cases["c9"][0]["attributes"] == {"region": "south"}
    assert workspace.summary(log) == before
    # The derived log behaves like any other: export and delete.
    out = StringIO()
    workspace.export_log(new["log_id"], "csv", out)
    assert out.getvalue().count("Escalate") == 4
    workspace.delete_log(new["log_id"])
    assert [entry["log_id"] for entry in workspace.list_logs()["logs"]] == [log]


def test_derived_log_keeps_original_events_collapsed_in_the_matching_analysis():
    from server import event_log_activity_mappings as mappings

    log = _csv_log()
    mapping = mappings.create(log, {"name": "Intake", "collapse_repeats": True,
        "groups": [{"label": "Review", "activities": ["Receive", "Escalate"]}]})
    mappings.apply_mapping(log, mapping["mapping_id"])
    cohort = {"follows": [{"from": "Review", "to": "Pay", "mode": "direct"}],
              "event_attribute": "region=south"}
    assert workspace.summary(log, cohort=cohort)["events"] == 8
    result = derive.derive(log, {"name": "South", "cohort": cohort})
    assert result["derived_from"]["cases_matched"] == 4
    assert result["copied"]["events"] == 10
    cases = dict(workspace.case_events(result["log"]["log_id"]))
    assert set(cases) == {"c6", "c7", "c8", "c9"}
    assert [event["activity"] for event in cases["c6"]] == ["Receive", "Escalate", "Pay"]
    assert cases["c6"][1]["attributes"] == {"region": "south"}


def test_object_centric_logs_keep_linked_objects_and_their_history():
    objects = [{"id": f"o{i}", "type": "order",
                "attributes": [{"name": "status", "value": "new", "time": "2024-01-01T00:00:00Z"},
                               {"name": "status", "value": "paid", "time": "2024-01-05T00:00:00Z"}]} for i in range(4)]
    objects += [{"id": "i1", "type": "item"}, {"id": "i2", "type": "item"}]
    events = [{"id": f"e{i}", "type": "pay" if i % 2 else "place", "time": f"2024-01-0{1 + i}T09:00:00Z",
               "relationships": [{"objectId": f"o{i}", "qualifier": "order"},
                                 {"objectId": "i1" if i < 2 else "i2", "qualifier": "item"}]} for i in range(4)]
    ocel = {"objectTypes": [{"name": "order", "attributes": [{"name": "status", "type": "string"}]},
                            {"name": "item", "attributes": []}],
            "eventTypes": [], "objects": objects, "events": events}
    log = workspace.import_log(BytesIO(json.dumps(ocel).encode()), name="o", source_format="ocel")["log_id"]
    r = derive.derive(log, {"name": "paid orders", "case_type": "order", "cohort": {"activity": "pay"}})
    assert r["derived_from"]["cases_matched"] == 2 and r["copied"] == {"events": 2, "objects": 4}
    types = {t["object_type"]: t["objects"] for t in workspace.object_types(r["log"]["log_id"])["object_types"]}
    assert types == {"order": 2, "item": 2}
    out = StringIO()
    workspace.export_log(r["log"]["log_id"], "ocel", out)
    exported = json.loads(out.getvalue())
    statuses = [o for o in exported["objects"] if o["id"] == "o1"][0]["attributes"]
    assert sorted(a["value"] for a in statuses) == ["new", "paid"]


def test_invalid_requests():
    log = _csv_log()
    for bad in (None, {}, {"name": "x"}, {"name": "x", "cohort": {}}, {"name": "x", "cohort": {"label": "only"}},
                {"name": "", "cohort": {"activity": "Pay"}}, {"name": "a<b", "cohort": {"activity": "Pay"}},
                {"name": "x", "cohort": {"activity": "Nowhere"}}, {"name": "x", "cohort": {"nope": 1}},
                {"name": "x", "cohort": {"activity": "Pay"}, "extra": 1}):
        with pytest.raises(workspace.EventLogWorkspaceError):
            derive.derive(log, bad)
    with pytest.raises(workspace.EventLogWorkspaceError):
        derive.derive("log_" + "0" * 16, {"name": "x", "cohort": {"activity": "Pay"}})
    assert len(workspace.list_logs()["logs"]) == 1


def test_log_limit(monkeypatch):
    log = _csv_log()
    monkeypatch.setattr(workspace, "MAX_LOGS", 1)
    with pytest.raises(workspace.EventLogWorkspaceError, match="delete an event log"):
        derive.derive(log, {"name": "x", "cohort": {"activity": "Pay"}})


def test_route_requires_the_dashboard(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
rows = ["case,activity,time"] + [f"c{i},{'A' if i else 'B'},2024-03-04T08:00:0{i}Z" for i in range(3)]
with TestClient(app) as client:
    owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    imported = client.post("/v1/event-logs/import?format=csv&name=t&case_column=case&activity_column=activity&timestamp_column=time",
                           content="\n".join(rows).encode(), headers={**owner, "X-OWG-Local-Storage-Confirmed": "true"})
    assert imported.status_code == 200, imported.text
    path = f"/v1/event-logs/{imported.json()['log_id']}/derive"
    body = {"name": "only A", "cohort": {"activity": "A"}}
    export = f"/v1/event-logs/{imported.json()['log_id']}/export/matching-cases"
    cohort = {"cohort": {"activity": "A"}}
    assert client.post(export + "?format=xes-attributes", json=cohort, headers=bearer).status_code == 403
    for fmt, suffix in (("csv-review", ".review.csv"), ("xes", ".xes"), ("xes-attributes", ".xes")):
        response = client.post(export + "?format=" + fmt, json=cohort, headers=owner)
        assert response.status_code == 200, response.text
        assert response.headers["content-disposition"].endswith(suffix + '"')
        assert response.headers["cache-control"] == "no-store"
    assert client.post(export, json={"cohort": {}}, headers=owner).status_code == 422
    explicit = {"cohort": {}, "case_type": "case", "include_unfiltered": True}
    assert client.post(export, json=explicit, headers=bearer).status_code == 403
    all_cases = client.post(export, json=explicit, headers=owner)
    assert all_cases.status_code == 200 and len(all_cases.text.splitlines()) == 4
    assert client.post(export, json={**explicit, "include_unfiltered": "true"}, headers=owner).status_code == 422
    assert client.post(export + "?format=ocel", json=cohort, headers=owner).status_code == 422

    assert client.post(path, json=body, headers=bearer).status_code == 403
    preview_body = {key: value for key, value in body.items() if key != "name"}
    assert client.post(path + "/preview", json=preview_body, headers=bearer).status_code == 403
    preview = client.post(path + "/preview", json=preview_body, headers=owner)
    assert preview.status_code == 200 and preview.json()["events"] == 2
    assert len(client.get("/v1/event-logs", headers=owner).json()["logs"]) == 1
    made = client.post(path, json=body, headers=owner)
    assert made.status_code == 200, made.text
    assert made.json()["log"]["events"] == 2
    assert len(client.get("/v1/event-logs", headers=owner).json()["logs"]) == 2
    assert client.post(path, json={"name": "all", "cohort": {}}, headers=owner).status_code == 422
print("ok")
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    done = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1], env=env,
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0 and done.stdout.strip().endswith("ok"), f"stdout={done.stdout}\nstderr={done.stderr}"



def test_matching_case_export_uses_the_mapping_but_writes_complete_original_traces():
    from server import event_log_activity_mappings as mappings
    from server.event_log_cohort_export import export_matching_cases

    log = _csv_log()
    mapping = mappings.create(log, {"name": "Intake", "collapse_repeats": True,
        "groups": [{"label": "Review", "activities": ["Receive", "Escalate"]}]})
    mappings.apply_mapping(log, mapping["mapping_id"])
    out = StringIO(newline="")
    export_matching_cases(log, {"cohort": {"follows": [{"from": "Review", "to": "Pay", "mode": "direct"}],
                                           "event_attribute": "region=south"}}, out)
    rows = list(csv.DictReader(StringIO(out.getvalue())))
    assert {row["case_id"] for row in rows} == {"c6", "c7", "c8", "c9"}
    assert len(rows) == 10
    assert [row["activity"] for row in rows if row["case_id"] == "c6"] == ["Receive", "Escalate", "Pay"]
    assert len(workspace.list_logs()["logs"]) == 1
    for cohort in ({}, {"label": "only"}, {"activity": "Absent"}):
        with pytest.raises(workspace.EventLogWorkspaceError):
            export_matching_cases(log, {"cohort": cohort}, StringIO())


def test_matching_xes_projection_preserves_only_selected_trace_attributes():
    from shared.event_log_formats import read_xes
    from server.event_log_cohort_export import export_matching_cases

    text = """<log>
      <trace><string key="concept:name" value="=included"/>
        <string key="region" value="A &amp; B"/>
        <event><string key="concept:name" value="Open"/>
          <date key="time:timestamp" value="2024-01-01T00:00:00Z"/>
          <string key="lifecycle:transition" value="start"/>
        </event>
        <event><string key="concept:name" value="Close"/>
          <date key="time:timestamp" value="2024-01-01T00:01:00Z"/>
          <string key="lifecycle:transition" value="complete"/>
        </event>
      </trace>
      <trace><string key="concept:name" value="excluded"/>
        <string key="region" value="PRIVATE_REGION"/>
        <event><string key="concept:name" value="Open"/>
          <date key="time:timestamp" value="2024-01-01T00:00:00Z"/></event>
      </trace></log>"""
    log = workspace.import_log(BytesIO(text.encode()), name="xes sample", source_format="xes")["log_id"]
    spec = {"cohort": {"activity": "Close"}}
    rich, plain, review, exact = (StringIO(newline="") for _ in range(4))
    for fmt, out in (("xes-attributes", rich), ("xes", plain), ("csv-review", review), ("csv", exact)):
        export_matching_cases(log, spec, out, export_format=fmt)
        assert "excluded" not in out.getvalue() and "PRIVATE_REGION" not in out.getvalue()
    trace, first, last = list(read_xes(BytesIO(rich.getvalue().encode())))
    assert trace.object_id == "=included" and trace.attributes == {"region": "A & B"}
    assert first.attributes == {"lifecycle:transition": "start"}
    assert last.attributes == {"lifecycle:transition": "complete"}
    assert all(row.attributes == {} for row in read_xes(BytesIO(plain.getvalue().encode())))
    assert list(csv.reader(StringIO(exact.getvalue())))[1][0] == "=included"
    assert list(csv.reader(StringIO(review.getvalue())))[1][0] == "'=included"
    assert len(workspace.list_logs()["logs"]) == 1


def test_a_time_window_trims_the_copy_to_its_events():
    log = _csv_log()                       # c0..c9 start on 4..13 March 08:00, one step per hour
    r = derive.derive(log, {"name": "week", "events_from": "2024-03-05T08:30:00Z", "events_before": "2024-03-07T00:00:00Z"})
    window = r["time_window"]
    assert window["events_from"] == "2024-03-05T08:30:00Z" and window["events_before"] == "2024-03-07T00:00:00Z"
    # c1 keeps only Pay (09:00); c2 keeps Receive and Pay; c0 and c3.. are outside.
    assert window["cases_with_events_in_window"] == 2 and r["copied"]["events"] == 3
    assert window["events_outside_left_out"] == 24 - 3            # 4 cases of 3 steps, 6 of 2
    paths = {tuple(v["path"]): v["cases"] for v in workspace.variants(r["log"]["log_id"])["variants"]}
    assert paths == {("Pay",): 1, ("Receive", "Pay"): 1}
    # Combined with a case filter, and open-ended.
    south = derive.derive(log, {"name": "late escalations", "cohort": {"activity": "Escalate"},
                                "events_from": "2024-03-12T00:00:00+01:00"})     # only c9 (13 March) is late
    assert south["copied"]["events"] == 3
    assert south["time_window"]["events_before"] is None and south["time_window"]["cases_with_events_in_window"] == 1
    assert "time_window" not in derive.derive(log, {"name": "esc", "cohort": {"activity": "Escalate"}})


def test_preview_and_copy_count_original_events_that_were_collapsed():
    from server import event_log_activity_mappings as mappings

    raw = b"""case,activity,time
c,Start,2024-03-04T08:00:00Z
c,Retry,2024-03-04T09:00:00Z
"""
    imported = workspace.import_log(BytesIO(raw), name="collapsed", source_format="csv",
                                    mapping={"case_column": "case", "activity_column": "activity",
                                             "timestamp_column": "time"})
    log = imported["log_id"]
    saved = mappings.create(log, {"name": "Collapsed attempts",
                                  "groups": [{"label": "Attempt", "activities": ["Start", "Retry"]}],
                                  "collapse_repeats": True})
    mappings.apply_mapping(log, saved["mapping_id"])
    selection = {"cohort": {"activity": "Attempt"}, "events_from": "2024-03-04T09:00:00Z"}
    preview = derive.preview(log, selection)
    assert preview["kept_cases"] == preview["events"] == preview["objects"] == 1
    assert preview["trimmed"] == 1
    assert len(workspace.list_logs()["logs"]) == 1
    copied = derive.derive(log, {**selection, "name": "Retry period"})
    assert copied["time_window"]["cases_with_events_in_window"] == 1
    assert copied["copied"] == {"events": 1, "objects": 1}


def test_time_window_validation():
    log = _csv_log()
    for bad, match in (({"name": "x", "events_from": "2024-03-05"}, "UTC offset"),
                       ({"name": "x", "events_from": "2024-03-06T00:00:00Z", "events_before": "2024-03-05T00:00:00Z"}, "before"),
                       ({"name": "x"}, "case filter or a time window"),
                       ({"name": "x", "events_from": "2030-01-01T00:00:00Z"}, "no event")):
        with pytest.raises(workspace.EventLogWorkspaceError, match=match):
            derive.derive(log, bad)
