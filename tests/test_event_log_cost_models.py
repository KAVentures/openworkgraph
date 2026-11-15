"""Saved cost models and the report's cost section."""
from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from io import BytesIO, StringIO

import pytest

from server import event_log_costs as costs
from server import event_log_derive as derive
from server import event_log_report as report
from server import event_log_workspace as workspace

START = datetime(2024, 3, 4, 8, tzinfo=timezone.utc)
MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time"}
MODEL = {"currency": "USD", "activity_costs": {"Receive": 2, "Check": 10.5}, "hourly_open_case_cost": 0}


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _log(name="invoices"):
    rows = []
    for i in range(10):
        path = ["Receive", "Check", "Pay"] if i < 6 else ["Receive", "Check", "Check", "Pay"]
        rows += [[f"c{i}", a, (START + timedelta(days=i, hours=k)).isoformat()] for k, a in enumerate(path)]
    out = StringIO()
    w = csv.writer(out)
    w.writerow(["case", "activity", "time"])
    w.writerows(rows)
    return workspace.import_log(BytesIO(out.getvalue().encode()), name=name, source_format="csv", mapping=MAPPING)["log_id"]


def test_save_read_replace_and_forget():
    log, other = _log("a"), _log("b")
    assert costs.saved_model(log) is None
    saved = costs.save_model(log, MODEL)
    assert saved["model"] == {"currency": "USD", "activity_costs": {"Receive": 2.0, "Check": 10.5},
                              "default_activity_cost": None, "hourly_open_case_cost": 0.0}
    assert costs.saved_model(log)["model"] == saved["model"] and costs.saved_model(other) is None
    costs.save_model(log, {"activity_costs": {"Pay": 1}})
    assert costs.saved_model(log)["model"]["activity_costs"] == {"Pay": 1.0}
    assert costs.delete_model(log)["deleted"] is True and costs.saved_model(log) is None
    assert costs.delete_model(log)["deleted"] is False


def test_invalid_models_and_unknown_logs():
    log = _log()
    for bad in (None, [], {}, {"activity_costs": {"Check": -1}}, {"activity_costs": {"Check": 1}, "cohort": {}},
                {"activity_costs": {"Check": 1}, "limit": 5}, {"currency": "usd", "activity_costs": {"Check": 1}}):
        with pytest.raises(workspace.EventLogWorkspaceError):
            costs.save_model(log, bad)
    for call in (lambda: costs.saved_model("log_" + "0" * 16), lambda: costs.save_model("log_" + "0" * 16, MODEL)):
        with pytest.raises(workspace.EventLogWorkspaceError):
            call()


def test_report_includes_a_cost_section_only_with_a_saved_model():
    log = _log()
    assert "<h2>Process cost</h2>" not in report.report_html(log)
    costs.save_model(log, MODEL)
    html = report.report_html(log)
    # Receive 10 * 2 + Check 14 * 10.5 = 167; Pay has no price.
    assert "<h2>Process cost</h2>" in html and "USD 167.00" in html
    assert "No price for Pay: 10 executions are left out of the totals." in html
    assert "The log does not record what work cost." in html
    filtered = report.build(log, cohort={"started_from": "2024-03-10T00:00:00Z"})
    assert filtered["costs"]["cases"] == 4 and filtered["costs"]["activity_cost"] == 4 * 2 + 8 * 10.5


def test_model_is_deleted_with_the_log_and_not_copied_to_derived_logs():
    log = _log()
    costs.save_model(log, MODEL)
    made = derive.derive(log, {"name": "slice", "cohort": {"started_from": "2024-03-10T00:00:00Z"}})
    assert "cost model" in made["not_copied"] and costs.saved_model(made["log"]["log_id"]) is None
    workspace.delete_log(log)
    conn = workspace._connect()
    try:
        assert conn.execute("SELECT count(*) FROM cost_models").fetchone()[0] == 0
    finally:
        conn.close()


def test_routes_require_the_dashboard(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
rows = ["case,activity,time"] + [f"c{i},A,2024-03-04T08:00:0{i}Z" for i in range(3)]
with TestClient(app) as client:
    owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    imported = client.post("/v1/event-logs/import?format=csv&name=t&case_column=case&activity_column=activity&timestamp_column=time",
                           content="\n".join(rows).encode(), headers={**owner, "X-OWG-Local-Storage-Confirmed": "true"})
    assert imported.status_code == 200, imported.text
    base = f"/v1/event-logs/{imported.json()['log_id']}"
    model = {"currency": "EUR", "activity_costs": {"A": 2}}
    assert client.get(base + "/cost-model", headers=bearer).status_code == 403
    assert client.post(base + "/cost-model", json=model, headers=bearer).status_code == 403
    assert client.get(base + "/cost-model", headers=owner).json()["saved"] is None
    assert client.post(base + "/cost-model", json=model, headers=owner).status_code == 200
    assert client.get(base + "/cost-model", headers=owner).json()["saved"]["model"]["activity_costs"] == {"A": 2.0}
    assert "EUR 6.00" in client.get(base + "/report.html", headers=owner).text
    assert client.post(base + "/cost-model", json={"activity_costs": {"A": 2}, "limit": 1}, headers=owner).status_code == 422
    assert client.post(base + "/cost-model/delete", headers=owner).json()["deleted"] is True
    assert "Process cost" not in client.get(base + "/report.html", headers=owner).text
print("ok")
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    done = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1], env=env,
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0 and done.stdout.strip().endswith("ok"), f"stdout={done.stdout}\nstderr={done.stderr}"
