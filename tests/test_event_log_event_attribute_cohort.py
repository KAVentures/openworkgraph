"""Case filters on event attributes (columns kept from a CSV import)."""
from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta, timezone
from io import BytesIO, StringIO

import pytest

from server import event_log_analytics as analytics
from server import event_log_cohorts as cohorts
from server import event_log_workspace as workspace

START = datetime(2024, 3, 4, 8, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _csv_log():
    """Ten cases; region is set on every event; priority only on the first event of cases 0-2."""
    rows = []
    for i in range(10):
        for k, activity in enumerate(["Receive", "Pay"]):
            rows.append([f"c{i}", activity, (START + timedelta(days=i, hours=k)).isoformat(),
                         "south" if i >= 6 else "north", "high" if (i < 3 and k == 0) else ""])
    out = StringIO()
    w = csv.writer(out)
    w.writerow(["case", "activity", "time", "region", "priority"])
    w.writerows(rows)
    mapping = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time",
               "attribute_columns": ["region", "priority"]}
    return workspace.import_log(BytesIO(out.getvalue().encode()), name="csv", source_format="csv", mapping=mapping)["log_id"]


def test_event_attribute_filters_csv_columns():
    log = _csv_log()
    assert analytics.kpis(log, cohort={"event_attribute": "region=south"})["cases"] == 4
    assert analytics.kpis(log, cohort={"event_attribute": " region = north "})["cases"] == 6
    # A value on any one event of the case is enough.
    high = analytics.kpis(log, cohort={"event_attribute": "priority=high"})
    assert high["cases"] == 3 and high["cohort"]["event_attribute"] == "priority=high"
    assert analytics.kpis(log, cohort={"event_attribute": "priority=high", "activity": "Pay"})["cases"] == 3
    assert analytics.kpis(log, cohort={"event_attribute": "region=west"})["cases"] == 0
    # The case-object filter does not see event attributes, as documented.
    assert analytics.kpis(log, cohort={"attribute": "region=south"})["cases"] == 0


def test_event_attribute_matches_ocel_values_as_stored_text():
    events = [{"id": f"e{i}", "type": "place", "time": f"2024-01-0{1 + i}T09:00:00Z",
               "attributes": [{"name": "amount", "value": 10 * i}, {"name": "rush", "value": i == 1}],
               "relationships": [{"objectId": f"o{i}", "qualifier": "order"}]} for i in range(3)]
    ocel = {"objectTypes": [{"name": "order", "attributes": []}],
            "eventTypes": [{"name": "place", "attributes": [{"name": "amount", "type": "integer"},
                                                             {"name": "rush", "type": "boolean"}]}],
            "objects": [{"id": f"o{i}", "type": "order"} for i in range(3)], "events": events}
    log = workspace.import_log(BytesIO(json.dumps(ocel).encode()), name="o", source_format="ocel")["log_id"]
    assert analytics.kpis(log, "order", cohort={"event_attribute": "amount=20"})["cases"] == 1
    # OCEL import keeps values as text, so a boolean matches its stored spelling.
    assert analytics.kpis(log, "order", cohort={"event_attribute": "rush=True"})["cases"] == 1


def test_event_attribute_validation():
    for bad in ({"event_attribute": "no-equals"}, {"event_attribute": "=x"}, {"event_attribute": 5},
                {"event_attribute": "a" * 201}):
        with pytest.raises(workspace.EventLogWorkspaceError):
            cohorts.parse_cohort(bad)
    assert cohorts.parse_cohort({"event_attribute": ""}) == {}
    assert cohorts.parse_cohort({"event_attribute": "k=v"}) == {"event_attribute": ("k", "v")}


def test_get_routes_accept_event_attribute(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
rows = ["case,activity,time,team"] + [f"c{i},A,2024-03-04T08:00:0{i}Z,{'x' if i else 'y'}" for i in range(3)]
with TestClient(app) as client:
    owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    imported = client.post("/v1/event-logs/import?format=csv&name=t&case_column=case&activity_column=activity&timestamp_column=time&attribute_column=team",
                           content="\n".join(rows).encode(), headers={**owner, "X-OWG-Local-Storage-Confirmed": "true"})
    assert imported.status_code == 200, imported.text
    base = f"/v1/event-logs/{imported.json()['log_id']}"
    summary = client.get(base + "/summary?event_attribute=team%3Dx", headers=owner)
    assert summary.status_code == 200, summary.text
    assert summary.json()["cases"] == 2 and summary.json()["cases_before_filter"] == 3
    assert client.get(base + "/kpis?event_attribute=nope", headers=owner).status_code == 422
print("ok")
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    done = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1], env=env,
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0 and done.stdout.strip().endswith("ok"), f"stdout={done.stdout}\nstderr={done.stderr}"
