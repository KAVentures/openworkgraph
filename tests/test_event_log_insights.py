"""Automatic insights over a saved event log."""
from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from io import BytesIO, StringIO

import pytest

from server import event_log_analytics as analytics
from server import event_log_insights as insights
from server import event_log_workspace as workspace

START = datetime(2024, 1, 1, 8, tzinfo=timezone.utc)
MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time"}


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _log(cases):
    """``cases``: list of [(activity, hours since case start), ...]."""
    rows = []
    for i, steps in enumerate(cases):
        for activity, hours in steps:
            rows.append([f"c{i:03d}", activity, (START + timedelta(days=i, hours=hours)).isoformat()])
    out = StringIO()
    w = csv.writer(out)
    w.writerow(["case", "activity", "time"])
    w.writerows(rows)
    return workspace.import_log(BytesIO(out.getvalue().encode()), name="i", source_format="csv", mapping=MAPPING)["log_id"]


def _cases():
    """40 cases: Approve -> Pay always waits 10 h; every 4th case checks twice; every 5th escalates (+30 h);
    the newer half is slower (an extra 12 h before Approve)."""
    out = []
    for i in range(40):
        t, steps = 0.0, [("Receive", 0.0)]
        t += 1
        steps.append(("Check", t))
        if i % 4 == 0:
            t += 1
            steps.append(("Check", t))
        if i % 5 == 0:
            t += 30
            steps.append(("Escalate", t))
        t += 12 if i >= 20 else 1
        steps.append(("Approve", t))
        t += 10
        steps.append(("Pay", t))
        out.append(steps)
    return out


def test_findings_are_ranked_and_reproducible():
    log = _log(_cases())
    r = insights.insights(log, {"sla_seconds": 24 * 3600, "limit": 30})
    assert r["format"] == insights.FORMAT and r["cases"] == 40 and not r["too_few_cases"]
    kinds = {}
    for f in r["findings"]:
        kinds.setdefault(f["kind"], f)  # the highest-ranked finding of each kind
    assert {"slow_transition", "repeated_activity", "costly_detour", "long_tail", "variant_spread",
            "slowing_down", "sla_breaches"} <= set(kinds)
    slow = kinds["slow_transition"]
    assert slow["numbers"]["from"] == "Approve" and slow["numbers"]["to"] == "Pay" and slow["cases"] == 40
    assert slow["follows"] == [{"from": "Approve", "to": "Pay", "mode": "direct", "negate": False}]
    rep = kinds["repeated_activity"]
    assert rep["numbers"]["activity"] == "Check" and rep["cases"] == 10 and rep["case_share"] == 0.25
    # The repeat rule reproduces exactly the cases it counts.
    assert analytics.kpis(log, cohort={"follows": rep["follows"]})["cases"] == 10
    det = kinds["costly_detour"]
    assert det["numbers"]["activity"] == "Escalate" and det["cases"] == 8 and det["cohort"] == {"activity": "Escalate"}
    assert analytics.kpis(log, cohort=det["cohort"])["cases"] == 8
    # The SLA count matches the KPI analysis for the same threshold.
    assert kinds["sla_breaches"]["cases"] == analytics.kpis(log, sla_seconds=24 * 3600)["sla"]["breached_cases"]
    assert kinds["slowing_down"]["numbers"]["newer_median_seconds"] > kinds["slowing_down"]["numbers"]["older_median_seconds"]
    assert [f["rank"] for f in r["findings"]] == list(range(1, len(r["findings"]) + 1))
    impacts = [f["impact_seconds"] for f in r["findings"]]
    assert impacts == sorted(impacts, reverse=True)
    assert all(f["caveat"] for f in r["findings"]) and "not causes" in r["interpretation"]


def test_limit_filter_and_small_logs():
    log = _log(_cases())
    assert len(insights.insights(log, {"limit": 2})["findings"]) == 2
    only_escalated = insights.insights(log, {"cohort": {"activity": "Escalate"}})
    assert only_escalated["cases"] == 8 and only_escalated["cohort"] == {"activity": "Escalate"}
    tiny = insights.insights(_log(_cases()[:3]))
    assert tiny["too_few_cases"] is True and tiny["findings"] == []
    for bad in ({"limit": 0}, {"sla_seconds": 0}, {"x": 1}, {"cohort": {"nope": 1}}):
        with pytest.raises(workspace.EventLogWorkspaceError):
            insights.insights(log, bad)


def test_route_requires_the_dashboard(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
rows = ["case,activity,time"]
for i in range(8):
    rows += [f"c{i},A,2024-03-0{1 + i % 8}T08:00:00Z", f"c{i},B,2024-03-0{1 + i % 8}T18:00:00Z"]
with TestClient(app) as client:
    owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    imported = client.post("/v1/event-logs/import?format=csv&name=t&case_column=case&activity_column=activity&timestamp_column=time",
                           content="\n".join(rows).encode(), headers={**owner, "X-OWG-Local-Storage-Confirmed": "true"})
    assert imported.status_code == 200, imported.text
    path = f"/v1/event-logs/{imported.json()['log_id']}/insights"
    assert client.post(path, json={}, headers=bearer).status_code == 403
    found = client.post(path, json={}, headers=owner)
    assert found.status_code == 200, found.text
    assert found.json()["findings"][0]["kind"] == "slow_transition"
    assert client.post(path, json={"limit": 99}, headers=owner).status_code == 422
print("ok")
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    done = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1], env=env,
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0 and done.stdout.strip().endswith("ok"), f"stdout={done.stdout}\nstderr={done.stderr}"
