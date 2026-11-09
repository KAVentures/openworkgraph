"""Process cost from a user-supplied cost model."""
from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from io import BytesIO, StringIO

import pytest

from server import event_log_costs as costs
from server import event_log_workspace as workspace

START = datetime(2024, 3, 4, 8, tzinfo=timezone.utc)
MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time", "resource_column": "who"}


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _log():
    """Six straight cases (Receive, Check, Pay; 2 h) and four that check twice (Receive, Check, Check, Pay; 3 h)."""
    rows = []
    for i in range(10):
        path = ["Receive", "Check", "Pay"] if i < 6 else ["Receive", "Check", "Check", "Pay"]
        who = {"Receive": "clerk", "Check": "Ana", "Pay": "bot"}
        for k, activity in enumerate(path):
            rows.append([f"c{i}", activity, (START + timedelta(days=i, hours=k)).isoformat(), who[activity]])
    out = StringIO()
    w = csv.writer(out)
    w.writerow(["case", "activity", "time", "who"])
    w.writerows(rows)
    return workspace.import_log(BytesIO(out.getvalue().encode()), name="invoices", source_format="csv", mapping=MAPPING)["log_id"]


def test_costs_from_activity_prices_and_holding_cost():
    log = _log()
    r = costs.costs(log, {"currency": "USD", "activity_costs": {"Receive": 2, "Check": 10.5, "Pay": 1},
                          "hourly_open_case_cost": 4})
    assert r["format"] == costs.FORMAT and r["currency"] == "USD" and r["cases"] == 10 and r["events"] == 34
    # Activities: 10*2 + 14*10.5 + 10*1 = 177. Holding: (6*2 h + 4*3 h) * 4 = 96.
    assert r["activity_cost"] == 177 and r["holding_cost"] == 96 and r["total_cost"] == 273
    assert r["repeated_execution_cost"] == 42 and r["repeated_execution_share"] == round(42 / 273, 6)
    # Straight case: 13.5 + 8 = 21.5; repeated: 24 + 12 = 36.
    assert r["cost_per_case"] == {"mean": 27.3, "median": 21.5, "p90": 36.0, "max": 36.0}
    by = {a["activity"]: a for a in r["activities"]}
    assert by["Check"]["cost"] == 147 and by["Check"]["repeated_executions"] == 4 and by["Check"]["repeated_execution_cost"] == 42
    assert by["Check"]["price_source"] == "model" and r["activities"][0]["activity"] == "Check"
    assert r["unpriced_activities"] == [] and r["unpriced_events"] == 0
    top = r["costliest_variants"][0]
    assert top["path"] == ["Receive", "Check", "Check", "Pay"] and top["cases"] == 4
    assert top["cost_per_case"] == 36 and top["total_cost"] == 144 and top["repeated_execution_cost_per_case"] == 10.5
    assert {x["resource"]: x["cost"] for x in r["resources"]} == {"Ana": 147, "clerk": 20, "bot": 10}
    assert "not verified rework" in r["interpretation"]


def test_unpriced_default_and_filter():
    log = _log()
    r = costs.costs(log, {"activity_costs": {"Check": 10}})
    assert r["unpriced_activities"] == [{"activity": "Pay", "events": 10}, {"activity": "Receive", "events": 10}]
    assert r["unpriced_events"] == 20 and r["activity_cost"] == 140 and r["holding_cost"] == 0
    assert {a["activity"]: a["price_source"] for a in r["activities"]} == {"Check": "model", "Pay": "none", "Receive": "none"}
    d = costs.costs(log, {"activity_costs": {"Check": 10}, "default_activity_cost": 1})
    assert d["unpriced_events"] == 0 and d["activity_cost"] == 160
    assert {a["activity"]: a["price_source"] for a in d["activities"]}["Pay"] == "default"
    f = costs.costs(log, {"activity_costs": {"Check": 10}, "cohort": {"follows": [
        {"from": "Check", "to": "Check", "mode": "direct", "negate": False}]}, "limit": 1})
    assert f["cases"] == 4 and f["activity_cost"] == 80 and len(f["costliest_variants"]) == 1


def test_invalid_models():
    log = _log()
    for bad in (None, {}, {"activity_costs": {"Check": -1}}, {"activity_costs": {"Check": "5"}},
                {"activity_costs": {"Check": True}}, {"activity_costs": {"": 1}}, {"activity_costs": {"Check": 2e9}},
                {"activity_costs": {"Check": 1}, "currency": "euro"}, {"activity_costs": {"Check": 1}, "x": 1},
                {"activity_costs": {f"a{i}": 1 for i in range(501)}},
                {"hourly_open_case_cost": float("inf")}, {"activity_costs": {"Check": 1}, "cohort": {"nope": 1}},
                {"activity_costs": {"Check": 1}, "cohort": {"activity": "Nowhere"}}):
        with pytest.raises(workspace.EventLogWorkspaceError):
            costs.costs(log, bad)


def test_route_requires_the_dashboard(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
rows = ["case,activity,time"] + [f"c{i},A,2024-03-04T08:00:0{i}Z" for i in range(3)] + [f"c{i},B,2024-03-04T09:00:0{i}Z" for i in range(3)]
with TestClient(app) as client:
    owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    imported = client.post("/v1/event-logs/import?format=csv&name=t&case_column=case&activity_column=activity&timestamp_column=time",
                           content="\n".join(rows).encode(), headers={**owner, "X-OWG-Local-Storage-Confirmed": "true"})
    assert imported.status_code == 200, imported.text
    path = f"/v1/event-logs/{imported.json()['log_id']}/costs"
    body = {"activity_costs": {"A": 1, "B": 2}}
    assert client.post(path, json=body, headers=bearer).status_code == 403
    found = client.post(path, json=body, headers=owner)
    assert found.status_code == 200, found.text
    assert found.json()["total_cost"] == 9
    assert client.post(path, json={}, headers=owner).status_code == 422
    dup = client.post(path, content=b'{"activity_costs": {"A": 1}, "activity_costs": {"B": 1}}',
                      headers={**owner, "Content-Type": "application/json"})
    assert dup.status_code == 422, dup.text
print("ok")
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    done = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1], env=env,
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0 and done.stdout.strip().endswith("ok"), f"stdout={done.stdout}\nstderr={done.stderr}"
