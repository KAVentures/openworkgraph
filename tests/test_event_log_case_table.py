"""Case table templates preserve ID scope and can be joined back."""
from __future__ import annotations

import csv
from io import BytesIO, StringIO
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import event_log_case_attributes as attributes
from server import event_log_case_table as case_table
from server import event_log_workspace as workspace


@pytest.fixture
def sample(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)
    payload = {"objectTypes": [], "eventTypes": [], "objects": [
        {"id": "=order", "type": "order",
         "attributes": [{"name": "Region", "value": "North, Europe"}, {"name": "Other", "value": "PRIVATE"}]},
        {"id": "order-empty", "type": "order"},
        {"id": "order-out", "type": "order"},
        {"id": "item", "type": "item"},
    ], "events": [
        {"id": "e1", "type": "Review", "time": "2024-01-01T00:00:00Z",
         "relationships": [{"objectId": "=order", "qualifier": ""}, {"objectId": "item", "qualifier": ""}]},
        {"id": "e2", "type": "Review", "time": "2024-01-02T00:00:00Z",
         "relationships": [{"objectId": "=order", "qualifier": ""}]},
        {"id": "e3", "type": "Close", "time": "2024-01-03T00:00:00Z",
         "relationships": [{"objectId": "order-out", "qualifier": ""}]},
    ]}
    return workspace.import_log(BytesIO(json.dumps(payload).encode()), name="Case table", source_format="ocel")["log_id"]


def rows(log, **spec):
    out = StringIO(newline="")
    case_table.export(log, {"case_type": "order", **spec}, out)
    return list(csv.reader(StringIO(out.getvalue())))


def test_template_includes_empty_cases_and_joins_back(sample, tmp_path):
    assert rows(sample, columns=["Department"]) == [
        ["case_id", "Department"], ["=order", ""], ["order-empty", ""], ["order-out", ""],
    ]
    template = rows(sample, cohort={"activity": "Review"}, columns=["Department"])
    assert template == [["case_id", "Department"], ["=order", ""]]
    template[1][1] = "Sales"
    path = tmp_path / "edited.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerows(template)
    record = attributes.add(sample, path, id_column="case_id", columns=["Department"], object_type="order")
    assert record["cases_updated"] == 1
    assert workspace.summary(sample, "order", {"attribute": "Department=Sales"})["cases"] == 1


def test_value_export_is_opt_in_and_review_csv_projects_formula_like_ids(sample):
    assert rows(sample, columns=["Region"], cohort={"activity": "Review"}) == [["case_id", "Region"], ["=order", ""]]
    exact = rows(sample, columns=["Region"], include_values=True, cohort={"activity": "Review"})
    assert exact == [["case_id", "Region"], ["=order", "North, Europe"]]
    review = rows(sample, columns=["Region"], include_values=True, spreadsheet_review=True, cohort={"activity": "Review"})
    assert review == [["case_id_review", "Region_review"], ["'=order", "North, Europe"]]
    assert rows(sample, case_type="item", columns=["Region"]) == [["case_id", "Region"], ["item", ""]]
    assert rows(sample, cohort={"activity": "Absent"}) == [["case_id"]]
    for bad in (["case_id"], ["Case_ID"], ["a", "A"], [" A"], ["A\nB"], ["x"] * 33):
        with pytest.raises(workspace.EventLogWorkspaceError):
            rows(sample, columns=bad)


def test_route_is_dashboard_only_and_returns_a_download(tmp_path):
    script = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
with TestClient(app) as client:
    owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    result = client.post("/v1/event-logs/import?format=csv&name=t&case_column=case&activity_column=activity&timestamp_column=time",
                         content=b"case,activity,time\nc1,A,2024-03-01T08:00:00Z\n",
                         headers={**owner, "X-OWG-Local-Storage-Confirmed": "true"})
    assert result.status_code == 200, result.text
    url = f"/v1/event-logs/{result.json()['log_id']}/case-table/export"
    assert client.post(url, json={}, headers=bearer).status_code == 403
    response = client.post(url, json={"columns":["Region"]}, headers=owner)
    assert response.status_code == 200 and response.text.splitlines() == ["case_id,Region", "c1,"]
    assert response.headers["content-disposition"].endswith('-case-table.csv"')
    assert response.headers["cache-control"] == "no-store"
    assert client.post(url, json={"cohort":[]}, headers=owner).status_code == 422
'''
    env = {**os.environ, "WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"),
           "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
           "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")}
    run = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
                         env=env, capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stderr
