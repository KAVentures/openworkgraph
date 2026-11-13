"""Offering the column mapping recorded with an earlier CSV import."""
from __future__ import annotations

from io import BytesIO

import pytest

from server import event_log_mapping_reuse as reuse
from server import event_log_workspace as workspace


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _import(name, text, **mapping):
    return workspace.import_log(BytesIO(text.encode()), name=name, source_format="csv", mapping=mapping)["log_id"]


def test_matches_by_required_columns_and_delimiter():
    first = _import("orders", "Order;Step;When;Who;Region\no1;A;13/03/2024 09:05;ana;EU\n",
                    case_column="Order", activity_column="Step", timestamp_column="When", resource_column="Who",
                    attribute_columns=["Region"], delimiter=";", naive_timestamp_offset="+01:00",
                    timestamp_format="%d/%m/%Y %H:%M")
    _import("other", "case,activity,time\nc1,A,2024-03-04T08:00:00Z\n",
            case_column="case", activity_column="activity", timestamp_column="time")
    found = reuse.matches({"columns": ["Who", "When", "Step", "Order", "New"], "delimiter": ";"})
    assert [m["log_id"] for m in found["matches"]] == [first]
    match = found["matches"][0]
    assert match["mapping"] == {"case_column": "Order", "activity_column": "Step", "timestamp_column": "When",
                                "resource_column": "Who", "attribute_columns": [], "delimiter": ";",
                                "naive_timestamp_offset": "+01:00", "timestamp_format": "%d/%m/%Y %H:%M"}
    assert match["missing_attribute_columns"] == ["Region"]
    # The same names with another delimiter, or without the resource column, do not match.
    assert reuse.matches({"columns": ["Who", "When", "Step", "Order"], "delimiter": ","})["matches"] == []
    assert reuse.matches({"columns": ["When", "Step", "Order"], "delimiter": ";"})["matches"] == []
    plain = reuse.matches({"columns": ["case", "activity", "time", "extra"]})["matches"]
    assert [m["name"] for m in plain] == ["other"]
    assert "column names only" in found["note"]


def test_newest_mapping_of_a_log_wins_and_invalid_settings_are_refused():
    log = _import("orders", "case,activity,time\nc1,A,2024-03-04T08:00:00Z\n",
                  case_column="case", activity_column="activity", timestamp_column="time")
    workspace.append_log(log, BytesIO(b"case,activity,time,who\nc2,A,2024-03-05T08:00:00Z,bo\n"), source_format="csv",
                         mapping={"case_column": "case", "activity_column": "activity", "timestamp_column": "time",
                                  "resource_column": "who"})
    found = reuse.matches({"columns": ["case", "activity", "time", "who"]})["matches"]
    assert len(found) == 1 and found[0]["batch"] == 1 and found[0]["mapping"]["resource_column"] == "who"
    older = reuse.matches({"columns": ["case", "activity", "time"]})["matches"]
    assert older == []  # the newest mapping of the log needs "who"
    for bad in ({}, {"columns": []}, {"columns": ["a"], "delimiter": "x"}, {"columns": ["a"], "extra": 1}):
        with pytest.raises(workspace.EventLogWorkspaceError):
            reuse.matches(bad)


def test_route(tmp_path):
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
    r = client.post("/v1/event-logs/import?format=csv&name=t&case_column=case&activity_column=activity&timestamp_column=time",
                    content=b"case,activity,time\nc1,A,2024-03-04T08:00:00Z\n", headers={**owner, "X-OWG-Local-Storage-Confirmed": "true"})
    assert r.status_code == 200, r.text
    body = {"columns": ["case", "activity", "time"]}
    assert client.post("/v1/event-logs/mapping-matches", json=body, headers=bearer).status_code == 403
    r = client.post("/v1/event-logs/mapping-matches", json=body, headers=owner)
    assert r.status_code == 200 and r.json()["matches"][0]["name"] == "t", r.text
    assert client.post("/v1/event-logs/mapping-matches", json={"columns": []}, headers=owner).status_code == 422
print("ok")
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    done = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1], env=env,
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0 and done.stdout.strip().endswith("ok"), f"stdout={done.stdout}\nstderr={done.stderr}"
