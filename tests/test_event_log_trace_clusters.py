"""Trace clustering of a saved event log, and applying a cluster as a case filter."""
from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from io import BytesIO, StringIO
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import event_log_analytics as analytics
from server import event_log_cohorts as cohorts
from server import event_log_trace_clusters as trace_clusters
from server import event_log_workspace as workspace

ROOT = Path(__file__).resolve().parents[1]
START = datetime(2024, 3, 1, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _cases():
    """Two ways of working: 30 standard orders (with small variations) and 12 returns that take far longer."""
    cases = {}
    for i in range(30):
        steps = ["Receive order", "Check stock", "Pick", "Ship", "Invoice"]
        if i % 3 == 1:
            steps = ["Receive order", "Check stock", "Pick", "Pack", "Ship", "Invoice"]
        elif i % 5 == 2:
            steps = ["Receive order", "Pick", "Ship", "Invoice"]
        cases[f"o{i:02d}"] = (steps, 1)
    for i in range(12):
        steps = ["Receive order", "Request return", "Inspect", "Refund"]
        if i % 4 == 3:
            steps = ["Receive order", "Request return", "Inspect", "Inspect", "Refund"]
        cases[f"r{i:02d}"] = (steps, 24)
    return cases


def _log(cases=None):
    out = StringIO()
    writer = csv.writer(out)
    writer.writerow(["case", "activity", "time"])
    for n, (case, (steps, hours)) in enumerate((cases or _cases()).items()):
        for k, activity in enumerate(steps):
            writer.writerow([case, activity, (START + timedelta(days=n, hours=k * hours)).isoformat()])
    return workspace.import_log(BytesIO(out.getvalue().encode()), name="orders", source_format="csv", mapping={
        "case_column": "case", "activity_column": "activity", "timestamp_column": "time"})["log_id"]


def test_two_ways_of_working_are_separated_and_described():
    log = _log()
    result = trace_clusters.clusters(log)
    assert result["cases"] == 42 and result["k_chosen_by"] == "silhouette" and result["k"] == 2
    assert result["silhouette"] > 0.4
    big, small = result["clusters"]
    assert (big["cases"], small["cases"]) == (30, 12) and abs(big["share"] + small["share"] - 1) < 1e-9
    assert {tuple(v["activities"]) for v in small["top_variants"]} == {
        ("Receive order", "Request return", "Inspect", "Refund"),
        ("Receive order", "Request return", "Inspect", "Inspect", "Refund")}
    more = {a["activity"]: a for a in small["more_common_activities"]}
    assert more["Request return"]["case_share_in_cluster"] == 1.0 and more["Request return"]["lift"] == 3.5
    assert "Ship" in {a["activity"] for a in small["less_common_activities"]}
    assert small["median_throughput_seconds"] > big["median_throughput_seconds"]
    assert small["mean_events_per_case"] == pytest.approx((9 * 4 + 3 * 5) / 12)
    assert "not process types" in result["interpretation"]
    # Deterministic: the same log gives the same clusters.
    assert trace_clusters.clusters(log)["clusters"] == result["clusters"]


def test_a_cluster_cohort_filters_the_other_analyses_to_its_cases():
    log = _log()
    small = trace_clusters.clusters(log)["clusters"][1]
    filtered = analytics.kpis(log, cohort=small["cohort"])
    benchmark = cohorts.benchmark(log, [small["cohort"], {}])
    assert benchmark["cohorts"][0]["cases"] == 12 and benchmark["cohorts"][1]["cases"] == 42
    assert filtered["cases"] == 12
    with pytest.raises(workspace.EventLogWorkspaceError):
        cohorts.parse_cohort({"variants": [[]]})
    with pytest.raises(workspace.EventLogWorkspaceError):
        cohorts.parse_cohort({"variants": "Receive order"})


def test_given_k_rare_variants_and_limits():
    log = _log()
    three = trace_clusters.clusters(log, {"k": 3})
    assert three["k_chosen_by"] == "given" and three["k"] == 3 and sum(c["cases"] for c in three["clusters"]) == 42
    capped = trace_clusters.clusters(log, {"max_variants": 10, "max_activities": 3})
    assert capped["variants"] == 5 and capped["variants_assigned_to_nearest"] == 0
    assert capped["activities_in_features"] == 3
    one = _log({"a": (["X", "Y"], 1), "b": (["X", "Y"], 1)})
    single = trace_clusters.clusters(one)
    assert single["k"] == 1 and single["clusters"][0]["cases"] == 2
    filtered = trace_clusters.clusters(log, {"cohort": {"activity": "Refund"}})
    assert filtered["cases"] == 12
    for bad in ({"k": 1}, {"max_variants": 1}, {"unknown": True}, {"cohort": {"nope": 1}}):
        with pytest.raises(workspace.EventLogWorkspaceError):
            trace_clusters.clusters(log, bad)


def test_rare_variants_join_their_nearest_cluster():
    cases = _cases()
    cases["odd1"] = (["Receive order", "Request return", "Inspect", "Refund", "Refund"], 24)
    cases["odd2"] = (["Receive order", "Check stock", "Pick", "Pack", "Pack", "Ship", "Invoice"], 1)
    log = _log(cases)
    result = trace_clusters.clusters(log, {"max_variants": 5, "k": 2})
    assert result["variants_clustered"] == 5 and result["variants_assigned_to_nearest"] == 2
    sizes = sorted(c["cases"] for c in result["clusters"])
    assert sum(sizes) == 44
    returns = next(c for c in result["clusters"] if any("Refund" in v["activities"] for v in c["top_variants"]))
    assert returns["cases"] == 13, "the odd return joins the returns, the odd order the orders"


def test_route_requires_the_dashboard(tmp_path):
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
rows = ["case,activity,time"]
for i in range(12):
    steps = ["A", "B", "C"] if i % 2 else ["A", "X", "Y", "Z"]
    rows += [f"c{i},{a},2024-03-01T0{k}:00:00Z" for k, a in enumerate(steps)]
with TestClient(app) as client:
    owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    imported = client.post("/v1/event-logs/import?format=csv&name=t&case_column=case&activity_column=activity&timestamp_column=time",
                           content="\n".join(rows).encode(), headers={**owner, "X-OWG-Local-Storage-Confirmed": "true"})
    assert imported.status_code == 200, imported.text
    log_id = imported.json()["log_id"]
    path = f"/v1/event-logs/{log_id}/trace-clusters"
    assert client.post(path, json={}, headers=bearer).status_code == 403
    found = client.post(path, json={}, headers=owner)
    assert found.status_code == 200, found.text
    assert [c["cases"] for c in found.json()["clusters"]] == [6, 6]
    assert client.post(path, json={"k": 99}, headers=owner).status_code == 422
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
