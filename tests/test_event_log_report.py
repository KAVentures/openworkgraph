"""Self-contained HTML report for a saved event log."""
from __future__ import annotations

import csv
import re
from datetime import datetime, timedelta, timezone
from io import BytesIO, StringIO

import pytest

from server import event_log_report as report
from server import event_log_workspace as workspace

START = datetime(2024, 3, 4, 8, tzinfo=timezone.utc)
MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time", "resource_column": "who"}


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _log(name="invoices \"Q1\" & co"):
    """Six cases take Receive → Approve → Pay, two repeat Approve, one goes Receive → Reject <b>x</b>."""
    rows = []
    for i in range(9):
        base = START + timedelta(days=i)
        case = f"c{i}"
        rows.append([case, "Receive", base.isoformat(), "clerk"])
        if i == 8:
            rows.append([case, "Reject <b>x</b>", (base + timedelta(hours=1)).isoformat(), "Ana"])
            continue
        rows.append([case, "Approve", (base + timedelta(hours=2)).isoformat(), "Ana"])
        if i >= 6:
            rows.append([case, "Approve", (base + timedelta(hours=3)).isoformat(), "Ben"])
        rows.append([case, "Pay", (base + timedelta(hours=4)).isoformat(), "bot"])
    out = StringIO()
    w = csv.writer(out)
    w.writerow(["case", "activity", "time", "who"])
    w.writerows(rows)
    return workspace.import_log(BytesIO(out.getvalue().encode()), name=name, source_format="csv", mapping=MAPPING)["log_id"]


def test_report_data_and_html():
    log = _log()
    data = report.build(log, sla_seconds=3 * 3600 + 1)
    assert data["format"] == report.FORMAT and data["log"]["log_id"] == log
    assert data["summary"]["cases"] == 9 and data["variants"]["distinct_variants"] == 3
    assert [v["cases"] for v in data["variants"]["variants"]] == [6, 2, 1]
    assert data["kpis"]["rework_cases"] == 2 and data["kpis"]["sla"]["breached_cases"] == 8
    html = report.render(data)
    assert html.startswith("<!doctype html>")
    # Log names and activities are user data and must be escaped.
    assert "invoices &quot;Q1&quot; &amp; co" in html and '"Q1"' not in html
    assert "Reject &lt;b&gt;x&lt;/b&gt;" in html and "<b>x</b>" not in html
    assert "Receive → Approve → Pay" in html
    # No scripts and no external resources: it opens the same offline.
    assert "<script" not in html.lower()
    assert not re.search(r"""(src|href)\s*=\s*["']?(https?:)?//""", html)
    assert "not verified rework" in html and "88.9%" in html  # 8 of 9 cases over the SLA


def test_filter_variant_limit_and_errors():
    log = _log("plain")
    data = report.build(log, cohort={"activity": "Pay"}, variant_limit=1)
    assert data["summary"]["cases"] == 8 and data["cases_before_filter"] == 9
    assert len(data["variants"]["variants"]) == 1
    html = report.render(data)
    assert "filtered to 8 of 9 cases" in html and "cover 75.0% of cases" in html
    for bad in (0, 51, True, 2.5):
        with pytest.raises(workspace.EventLogWorkspaceError):
            report.build(log, variant_limit=bad)
    with pytest.raises(workspace.EventLogWorkspaceError):
        report.build("log_" + "0" * 16)


def test_route_requires_the_dashboard_and_serves_a_download(tmp_path):
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
    log_id = imported.json()["log_id"]
    path = f"/v1/event-logs/{log_id}/report.html"
    assert client.get(path, headers=bearer).status_code == 403
    assert client.get(path).status_code in (401, 403)
    r = client.get(path + "?sla_seconds=60&variants=5&activity=B", headers=owner)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/html")
    assert r.headers["content-disposition"] == f'attachment; filename="{log_id}-report.html"'
    assert "default-src 'none'" in r.headers["content-security-policy"]
    assert "A → B" in r.text and "<script" not in r.text
    assert client.get(path + "?variants=0", headers=owner).status_code == 422
    assert client.get("/v1/event-logs/log_" + "0" * 16 + "/report.html", headers=owner).status_code == 422
print("ok")
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    done = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1], env=env,
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0 and done.stdout.strip().endswith("ok"), f"stdout={done.stdout}\nstderr={done.stderr}"


def test_slowest_transitions_need_enough_cases_and_honour_the_filter():
    log = _log("plain")
    # Receive → Approve and Approve → Pay occur in 8 cases with a 2 h median gap; the repeated
    # Approve → Approve (2 cases) and Receive → Reject (1 case) are below the 3-case floor.
    slow = report.slowest_transitions(log)
    assert slow["minimum_cases"] == 3
    assert [(t["from"], t["to"], t["cases"], t["median_gap_seconds"]) for t in slow["transitions"]] == [
        ("Approve", "Pay", 8, 7200.0), ("Receive", "Approve", 8, 7200.0)]
    assert slow["transitions"][0]["p90_gap_seconds"] == 7200.0
    filtered = report.slowest_transitions(log, cohort={"activity": "Reject"})
    assert filtered["transitions"] == [] and filtered["cases_before_filter"] == 9
    html = report.report_html(log)
    assert "<h2>Slowest transitions</h2>" in html and "Approve → Pay" in html
    assert "not measured work or verified waiting" in html
