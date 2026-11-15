"""The synthetic order-to-cash sample log."""
from __future__ import annotations

import json

import pytest

from server import event_log_analytics as analytics
from server import event_log_sample as sample
from server import event_log_workspace as workspace


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def test_document_is_deterministic_and_synthetic():
    first, second = sample.ocel_document(), sample.ocel_document()
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert json.dumps(sample.ocel_document(seed=1)) != json.dumps(first)
    types = {t["name"] for t in first["objectTypes"]}
    assert types == {"order", "item", "delivery", "invoice", "payment"}
    activities = {t["name"] for t in first["eventTypes"]}
    # The order-to-cash domain pack's activities are all present.
    assert {"Create order", "Approve credit", "Change order", "Pick item", "Ship", "Create invoice",
            "Receive payment"} <= activities


def test_import_has_the_patterns_and_is_not_duplicated():
    r = sample.import_sample()
    assert r["synthetic"] is True and r["already_present"] is False and r["log"]["name"] == sample.NAME
    log = r["log"]["log_id"]
    assert workspace.summary(log, "order")["cases"] == sample.ORDERS
    activities = {a["activity"]: a for a in workspace.activities(log, "order")["activities"]}
    assert 0.05 <= activities["Put on credit hold"]["cases"] / sample.ORDERS <= 0.15
    assert activities["Change order"]["repeated_in_cases"] > 0
    kpis = analytics.kpis(log, "order")
    assert kpis["rework_rate"] > 0
    paid = workspace.summary(log, "order", cohort={"activity": "Receive payment"})["cases"]
    assert 0 < sample.ORDERS - paid < 50  # some invoices are still open
    held = analytics.kpis(log, "order", cohort={"activity": "Put on credit hold"})["throughput_seconds"]["p50"]
    assert held > kpis["throughput_seconds"]["p50"]  # credit holds take longer
    again = sample.import_sample()
    assert again["already_present"] is True and again["log"]["log_id"] == log
    assert len(workspace.list_logs()["logs"]) == 1


def test_route_requires_dashboard_and_storage_confirmation(tmp_path):
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
    confirm = {"X-OWG-Local-Storage-Confirmed": "true"}
    assert client.post("/v1/event-logs/sample", headers={**bearer, **confirm}).status_code == 403
    assert client.post("/v1/event-logs/sample", headers=owner).status_code == 403
    made = client.post("/v1/event-logs/sample", headers={**owner, **confirm})
    assert made.status_code == 200, made.text
    assert made.json()["log"]["name"].startswith("Sample:") and made.json()["synthetic"] is True
    assert client.post("/v1/event-logs/sample", headers={**owner, **confirm}).json()["already_present"] is True
print("ok")
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    done = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1], env=env,
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0 and done.stdout.strip().endswith("ok"), f"stdout={done.stdout}\nstderr={done.stderr}"
