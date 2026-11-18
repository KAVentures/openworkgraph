"""Case attribute names, numeric ranges and common values for filter suggestions."""
from __future__ import annotations

import json
from io import BytesIO

import pytest

from server import event_log_case_attribute_values as cav
from server import event_log_case_attributes as case_attributes
from server import event_log_workspace as workspace


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def test_lists_names_values_and_numeric_ranges(tmp_path):
    rows = ["case,activity,time"] + [f"c{i},A,2024-03-{1 + i:02d}T08:00:00Z" for i in range(6)]
    log = workspace.import_log(BytesIO("\n".join(rows).encode()), name="t", source_format="csv",
                               mapping={"case_column": "case", "activity_column": "activity", "timestamp_column": "time"})["log_id"]
    assert cav.values(log)["attributes"] == []
    table = tmp_path / "t.csv"
    table.write_text("id,Region,amount,Note\n" + "\n".join(
        f"c{i},{'EU' if i < 4 else 'US'},{100 * (i + 1)},{'x' * 150 if i == 0 else ''}" for i in range(6)) + "\n")
    case_attributes.add(log, table, id_column="id", columns=["Region", "amount", "Note"])
    r = cav.values(log)
    by = {a["name"]: a for a in r["attributes"]}
    assert r["cases"] == 6 and list(by)[:2] == ["Region", "amount"]
    assert by["Region"]["numeric"] is False and by["Region"]["top_values"] == [{"value": "EU", "cases": 4}, {"value": "US", "cases": 2}]
    assert by["amount"]["numeric"] is True and (by["amount"]["min"], by["amount"]["max"]) == (100, 600)
    assert by["Note"]["top_values"] == [], "values longer than 100 characters are not suggested"


def test_ocel_case_notion_and_route(tmp_path):
    doc = {"objectTypes": [{"name": "order", "attributes": []}, {"name": "item", "attributes": []}],
           "eventTypes": [{"name": "A", "attributes": []}],
           "objects": [{"id": "o1", "type": "order", "attributes": [{"name": "prio", "time": "2024-01-01T00:00:00Z", "value": "high"}]},
                       {"id": "i1", "type": "item", "attributes": [{"name": "colour", "time": "2024-01-01T00:00:00Z", "value": "red"}]}],
           "events": [{"id": "e1", "type": "A", "time": "2024-01-01T00:00:00Z", "attributes": [],
                       "relationships": [{"objectId": "o1", "qualifier": ""}, {"objectId": "i1", "qualifier": ""}]}]}
    log = workspace.import_log(BytesIO(json.dumps(doc).encode()), name="o", source_format="ocel")["log_id"]
    assert [a["name"] for a in cav.values(log, "order")["attributes"]] == ["prio"]
    assert [a["name"] for a in cav.values(log, "item")["attributes"]] == ["colour"]
    import os
    import subprocess
    import sys
    from pathlib import Path
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
with TestClient(app) as client:
    owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    log_id = client.post("/v1/event-logs/import?format=csv&name=t&case_column=case&activity_column=activity&timestamp_column=time",
                         content=b"case,activity,time\nc1,A,2024-03-04T08:00:00Z\n", headers={**owner, "X-OWG-Local-Storage-Confirmed": "true"}).json()["log_id"]
    path = f"/v1/event-logs/{log_id}/case-attribute-values"
    assert client.get(path, headers=bearer).status_code == 403
    r = client.get(path, headers=owner)
    assert r.status_code == 200 and r.json()["attributes"] == [], r.text
    assert client.get(path + "?search=Region", headers=bearer).status_code == 403
    assert client.get(path + "?search=Region", headers=owner).json()["names_total"] == 0
    assert client.get(path + "?search=%20", headers=owner).status_code == 422

    assert client.get("/v1/event-logs/log_" + "0" * 16 + "/case-attribute-values", headers=owner).status_code == 422
print("ok")
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    done = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1], env=env,
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0 and done.stdout.strip().endswith("ok"), f"stdout={done.stdout}\nstderr={done.stderr}"


def test_search_finds_literal_keys_beyond_default_limit_and_keeps_json_finite():
    traces = []
    for case in range(6):
        attributes = "".join(f'<string key="a{case}-{i:02d}" value="value"/>' for i in range(18))
        nonfinite = "Infinity" if case % 2 else "NaN"
        attributes += f'<string key="NonFinite" value="{nonfinite}"/>'
        if case == 5:
            attributes += '<string key="zTail%_Feature" value="present"/>'
        traces.append(f'<trace><string key="concept:name" value="c{case}"/>{attributes}'
                      '<event><string key="concept:name" value="A"/>'
                      '<date key="time:timestamp" value="2024-01-01T00:00:00Z"/></event></trace>')
    log = workspace.import_log(BytesIO(("<log>" + "".join(traces) + "</log>").encode()),
                                name="wide attributes", source_format="xes")["log_id"]
    initial = cav.values(log)
    assert initial["names_total"] == 110 and initial["names_truncated"]
    assert all(row["name"] != "zTail%_Feature" for row in initial["attributes"])
    exact = cav.values(log, search="TAIL%_")
    assert exact["names_total"] == 1 and not exact["names_truncated"]
    assert exact["attributes"][0]["name"] == "zTail%_Feature"
    assert exact["attributes"][0]["top_values"] == [{"value": "present", "cases": 1}]
    finite_only = cav.values(log, search="NonFinite")["attributes"][0]
    assert not finite_only["numeric"] and "min" not in finite_only and "max" not in finite_only
    json.dumps(initial, allow_nan=False)
    broad = cav.values(log, search="a")
    assert broad["names_total"] > len(broad["attributes"]) == cav.MAX_SEARCH_NAMES
