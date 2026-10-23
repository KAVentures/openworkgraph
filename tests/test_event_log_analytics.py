"""Conformance, KPI and root-cause analytics for the event-log workspace (roadmap #326 C)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import event_log_analytics as analytics
from server import event_log_workspace as workspace

ROOT = Path(__file__).resolve().parents[1]
MODEL = {"format": "openworkgraph.reference-model.v1", "paths": [["Receive", "Check", "Approve", "Close"]]}


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _claims_csv(cases: int = 60) -> str:
    """Every third case is slow with rework (Fix, Check again); every tenth skips Approve."""
    rows = ["case,activity,time,who"]
    for c in range(cases):
        slow = c % 3 == 0
        steps = ["Receive", "Check", *(["Fix", "Check"] if slow else []), "Approve", "Close"]
        if c % 10 == 0:
            steps.remove("Approve")
        t = datetime(2024, 1, 1 + c % 20, 8, tzinfo=timezone.utc)
        for step in steps:
            t += timedelta(hours=3 if slow else 0.25)
            who = "bot" if step == "Check" else ("ann" if slow else "bob")
            rows.append(f"C{c},{step},{t.isoformat().replace('+00:00', 'Z')},{who}")
    return "\n".join(rows)


def _log(text: str | None = None, fmt: str = "csv"):
    mapping = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time", "resource_column": "who"}
    return workspace.import_log(BytesIO((text or _claims_csv()).encode()), name="claims", source_format=fmt,
                                mapping=mapping if fmt == "csv" else None)["log_id"]


def test_alignment_is_optimal_and_counts_log_and_model_moves():
    assert analytics._align(list("ABC"), list("ABC")) == (0, [("sync", "A"), ("sync", "B"), ("sync", "C")])
    cost, moves = analytics._align(list("AC"), list("ABC"))
    assert cost == 1 and ("model", "B") in moves
    cost, moves = analytics._align(list("ABXC"), list("ABC"))
    assert cost == 1 and ("log", "X") in moves
    cost, _ = analytics._align(list("BA"), list("AB"))  # swap = one log move + one model move
    assert cost == 2
    assert analytics._align([], list("AB"))[0] == 2


def test_conformance_fitness_and_deviations():
    log_id = _log()
    result = analytics.conformance(log_id, MODEL)
    assert result["cases"] == 60 and result["aligned_cases"] == 60
    # 34 fast cases with Approve fit; 4 fast cases skip it; 20 slow cases have Fix + extra Check (2 of them also skip).
    assert result["fitting_cases"] == 36
    skipped = {s["activity"]: s["cases"] for s in result["skipped_model_activities"]}
    extra = {s["activity"]: s["cases"] for s in result["extra_observed_activities"]}
    assert skipped == {"Approve": 6} and extra == {"Fix": 20, "Check": 20}
    assert 0 < result["fitness"] < 1 and result["reference_model_is_user_supplied"] is True
    alternatives = {**MODEL, "paths": MODEL["paths"] + [["Receive", "Check", "Fix", "Check", "Approve", "Close"]]}
    assert analytics.conformance(log_id, alternatives)["fitting_cases"] == 54


@pytest.mark.parametrize("model", [
    {}, {"format": "other", "paths": [["A"]]}, {"format": "openworkgraph.reference-model.v1", "paths": []},
    {"format": "openworkgraph.reference-model.v1", "paths": [["A", 3]]},
    {"format": "openworkgraph.reference-model.v1", "paths": [["A"]] * 11},
    {"format": "openworkgraph.reference-model.v1", "paths": [["x" * 201]]},
])
def test_invalid_reference_models_are_refused(model):
    log_id = _log()
    with pytest.raises(workspace.EventLogWorkspaceError):
        analytics.conformance(log_id, model)


def test_kpis_throughput_rework_sla_and_declared_automation():
    log_id = _log()
    result = analytics.kpis(log_id, sla_seconds=4 * 3600, automation_resources=["bot"])
    assert result["cases"] == 60
    assert result["rework_cases"] == 20 and result["rework_rate"] == pytest.approx(1 / 3, abs=1e-6)
    assert result["sla"] == {"seconds": 14400, "breached_cases": 20, "breach_rate": pytest.approx(1 / 3, abs=1e-6)}
    automation = result["automation"]
    assert automation["automated_events"] == 80 and automation["events"] == 274
    assert result["throughput_seconds"]["p50"] == 45 * 60
    assert sum(week["cases"] for week in result["completed_per_week"]) == 60
    assert analytics.kpis(log_id)["automation"] is None and "sla" not in analytics.kpis(log_id)
    with pytest.raises(workspace.EventLogWorkspaceError):
        analytics.kpis(log_id, automation_resources=[f"r{i}" for i in range(51)])
    with pytest.raises(workspace.EventLogWorkspaceError):
        analytics.kpis(log_id, sla_seconds=0)


def test_newcombe_interval_matches_published_example():
    # Newcombe (1998), Statistics in Medicine 17:873, method 10: 56/70 vs 48/80.
    d, low, high = analytics.risk_difference(56, 70, 48, 80)
    assert d == pytest.approx(0.2)
    assert low == pytest.approx(0.0524, abs=5e-4) and high == pytest.approx(0.3339, abs=5e-4)


def test_root_cause_ranks_associations_with_intervals_not_causes():
    log_id = _log()
    result = analytics.root_cause(log_id, "duration_over", threshold_seconds=5 * 3600)
    assert result["outcome_cases"] == 20 and result["associations_are_not_causes"] is True
    top = {(a["feature"]["kind"], a["feature"]["value"]): a for a in result["associations"]}
    fix = top[("activity", "Fix")]
    assert fix["risk_difference"] == 1.0 and fix["holds_after_bonferroni"] is True and fix["ci95"][0] > 0.8
    assert top[("resource", "bob")]["risk_difference"] == -1.0
    # Present in every case: no contrast, so never reported.
    assert ("activity", "Receive") not in top and ("resource", "bot") not in top
    approve = top[("activity", "Approve")]
    assert approve["holds_after_bonferroni"] is False and approve["ci95"][0] < 0 < approve["ci95"][1]
    assert result["associations"][0]["holds_after_bonferroni"] is True


def test_root_cause_outcome_kinds_and_validation():
    log_id = _log()
    rework = analytics.root_cause(log_id, "rework")
    assert rework["outcome_cases"] == 20
    contains = analytics.root_cause(log_id, "contains_activity", activity="Fix")
    assert contains["outcome_cases"] == 20
    assert all(a["feature"]["value"] != "Fix" for a in contains["associations"])  # target excluded
    assert analytics.root_cause(log_id, "ends_with", activity="Close")["note"]  # no variation
    for kwargs in ({"outcome": "duration_over"}, {"outcome": "contains_activity"}, {"outcome": "guess"}):
        with pytest.raises(workspace.EventLogWorkspaceError):
            analytics.root_cause(log_id, **kwargs)


def test_root_cause_uses_object_attributes_from_ocel():
    objects, events = [], []
    for i in range(40):
        region = "EU" if i % 2 else "US"
        objects.append({"id": f"o{i}", "type": "order", "attributes": [{"name": "region", "value": region,
                                                                         "time": "2024-01-01T00:00:00Z"}]})
        events.append({"id": f"a{i}", "type": "place", "time": "2024-01-01T09:00:00Z",
                       "relationships": [{"objectId": f"o{i}", "qualifier": "order"}]})
        end = "2024-01-03T09:00:00Z" if region == "EU" else "2024-01-01T10:00:00Z"
        events.append({"id": f"b{i}", "type": "ship", "time": end, "relationships": [{"objectId": f"o{i}", "qualifier": "order"}]})
    log_id = _log(json.dumps({"objectTypes": [{"name": "order", "attributes": []}], "eventTypes": [],
                              "objects": objects, "events": events}), "ocel")
    result = analytics.root_cause(log_id, "duration_over", "order", threshold_seconds=86400)
    found = {a["feature"]["value"]: a for a in result["associations"] if a["feature"]["kind"] == "attribute"}
    assert found["region=EU"]["risk_difference"] == 1.0 and found["region=US"]["risk_difference"] == -1.0


def test_real_http_analytics_routes(tmp_path):
    code = r'''
import json
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth

csv_body = "\n".join(["case,activity,time,who"] + [
    f"c{i},{a},2024-01-01T0{n}:00:00Z, bot " for i in range(12) for n, a in enumerate(["A", "B", "C"] if i % 2 else ["A", "C"])
]).encode()
with TestClient(app) as client:
    headers = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    made = client.post("/v1/event-logs/import?format=csv&name=t&case_column=case&activity_column=activity&timestamp_column=time&resource_column=who",
                       content=csv_body, headers={**headers, "x-owg-local-storage-confirmed": "true"})
    assert made.status_code == 200, made.text
    base = "/v1/event-logs/" + made.json()["log_id"]
    model = {"format": "openworkgraph.reference-model.v1", "paths": [["A", "B", "C"]]}
    assert client.post(base + "/conformance", json=model).status_code == 401
    ok = client.post(base + "/conformance", json=model, headers=headers)
    assert ok.status_code == 200 and ok.json()["fitting_cases"] == 6, ok.text
    assert client.post(base + "/conformance", content=b"x", headers={**headers, "Content-Type": "text/plain"}).status_code == 415
    assert client.post(base + "/conformance", content=b"{", headers={**headers, "Content-Type": "application/json"}).status_code == 422
    assert client.post(base + "/conformance", content=b" " * 70000, headers={**headers, "Content-Type": "application/json"}).status_code == 413
    kpis = client.get(base + "/kpis?sla_seconds=3600&automation_resources=bot,%20system", headers=headers)
    assert kpis.status_code == 200 and kpis.json()["sla"]["breached_cases"] == 6, kpis.text
    assert kpis.json()["automation"]["resources"] == ["bot", "system"]
    exact = client.get(base + "/kpis", params=[("automation_resource", " bot "), ("automation_resource", "bot,system")], headers=headers)
    assert exact.status_code == 200, exact.text
    assert exact.json()["automation"]["resources"] == [" bot ", "bot,system"]
    assert exact.json()["automation"]["automated_events"] == 30
    cause = client.get(base + "/root-cause?outcome=contains_activity&activity=B", headers=headers)
    assert cause.status_code == 200 and cause.json()["outcome_cases"] == 6, cause.text
    assert client.get(base + "/root-cause?outcome=duration_over", headers=headers).status_code == 422
    script = client.get("/event-log-workspace.js")
    assert script.status_code == 200 and "javascript" in script.headers["content-type"]
'''
    env = os.environ.copy()
    env.update({
        "WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"),
        "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
        "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json"),
    })
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"


def test_dashboard_client_is_wired_and_parses_reference_paths():
    html = (ROOT / "dashboard/index.html").read_text(encoding="utf-8")
    script = (ROOT / "dashboard/event_log_workspace.js").read_text(encoding="utf-8")
    assert '<script src="/event-log-workspace.js"></script>' in html
    assert "innerHTML" not in script and "localStorage" not in script
    assert "'X-OWG-Local-Storage-Confirmed':'true'" in script
    import shutil
    if not shutil.which("node"):
        return
    check = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const scope={window:{},document:{}};
vm.runInNewContext(fs.readFileSync('dashboard/event_log_workspace.js','utf8'),scope);
const m=scope.window.__owgEventLogModel;
assert.deepEqual(JSON.parse(JSON.stringify(m.parsePaths('A > B → C\n\nA>C'))),{format:'openworkgraph.reference-model.v1',paths:[['A','B','C'],['A','C']]});
assert.throws(()=>m.parsePaths('  '));
assert.deepEqual(JSON.parse(JSON.stringify(m.parsePaths('{"format":"openworkgraph.reference-model.v1","paths":[[" Review > ","Close→ "]]}'))).paths,[[' Review > ','Close→ ']]);
assert.deepEqual(JSON.parse(JSON.stringify(m.csvHeader(' case ,"step\nname",time\nx',','))),[' case ','step\nname','time']);
assert.deepEqual(JSON.parse(JSON.stringify(m.csvHeader('﻿"case, id";"step""s";time\nx','; '.trim()))),['case, id','step"s','time']);
assert.equal(m.detectFormat('log.XES'),'xes');assert.equal(m.detectFormat('o.jsonocel'),'ocel');assert.equal(m.detectFormat('a.tsv'),'csv');
assert.equal(m.duration(90),'90 s');assert.equal(m.duration(3600),'60 min');assert.equal(m.duration(86400*3),'3.0 d');
"""
    result = subprocess.run(["node", "-e", check], cwd=ROOT, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_literal_labels_remain_distinct_in_conformance_and_resource_kpis():
    log_id = _log("case,activity,time,who\n"
                  "Case,Review,2024-01-01T00:00:00Z,bot\n"
                  " Case,Review ,2024-01-01T00:00:00Z, bot\n"
                  "Case ,Review,2024-01-01T00:00:00Z,bot \n")
    model = {"format": "openworkgraph.reference-model.v1", "paths": [["Review"]]}
    result = analytics.conformance(log_id, model)
    assert result["cases"] == 3 and result["fitting_cases"] == 2
    assert [(v["activity"], v["cases"]) for v in result["extra_observed_activities"]] == [("Review ", 1)]
    for resource in ["bot", " bot", "bot "]:
        metrics = analytics.kpis(log_id, automation_resources=[resource])
        assert metrics["cases"] == 3
        assert metrics["automation"]["events"] == 3
        assert metrics["automation"]["automated_events"] == 1
