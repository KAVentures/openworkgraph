"""Monitoring of customer-run automations from imported run reports (roadmap #326 D)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
from zipfile import ZipFile

import pytest

from server import automation_monitoring as am
from server import event_log_workspace as workspace

ROOT = Path(__file__).resolve().parents[1]
START = datetime(2024, 3, 1, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(am, "DATA_DIR", tmp_path)
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _run(i, status, day, extra_step=False):
    t = START + timedelta(days=day, minutes=i)
    steps = [{"step_id": "step_001", "activity": "Receive", "status": "completed", "at": t.isoformat()}]
    second = {"succeeded": "completed", "handed_to_human": "handed_to_human", "failed": "failed",
              "cancelled": "skipped", "running": "completed"}[status]
    steps.append({"step_id": "step_002", "activity": "Validate", "status": second, "at": (t + timedelta(minutes=2)).isoformat()})
    if extra_step:
        steps.append({"step_id": "step_x", "activity": "Improvised", "status": "completed", "at": (t + timedelta(minutes=3)).isoformat()})
    if status == "succeeded":
        steps.append({"step_id": "step_003", "activity": "Close", "status": "completed", "at": (t + timedelta(minutes=4)).isoformat()})
    return {"run_id": f"r{i}", "started_at": t.isoformat(), "status": status, "steps": steps,
            "finished_at": None if status == "running" else (t + timedelta(minutes=5)).isoformat(),
            "outcome": {"verified": i % 2 == 0, "source": "crm"} if status == "succeeded" else {}}


def _report(runs, **deployment):
    return {"format": am.FORMAT, "deployment": {"name": "Refund agent", "runtime": "ai-sdk", **deployment}, "runs": runs}


def _history():
    """Baseline (days 0-20): 40 succeeded. Recent (days 25-28): 10 succeeded, 10 handed to a person, 1 running."""
    runs = [_run(i, "succeeded", i // 2) for i in range(40)]
    runs += [_run(100 + i, "succeeded" if i < 10 else "handed_to_human", 25 + i // 7) for i in range(20)]
    runs.append(_run(200, "running", 28))
    return runs


def _spec():
    from server.process_agent_starter import build_agent_starter
    packet = {
        "format": "openworkgraph.proposed-process-design.v1", "status": "human_reviewed_draft_not_approved_policy",
        "business_goal_declared_by_user": "Resolve refunds", "user_reviewed_export": True,
        "approved_business_policy": False, "authorized_agent_actions": False, "agent_execution_triggered": False,
        "source_case_ids_included": False, "uploaded_data_retained_by_owg": False,
        "as_is": {"path": ["Receive", "Close"], "source_observation_not_policy": True},
        "to_be": {"proposed_path": ["Receive", "Validate", "Close"], "proposed_not_deployed": True},
    }
    with ZipFile(BytesIO(build_agent_starter(packet))) as archive:
        return json.loads(archive.read("workflow.spec.json"))


def test_monitor_reports_rates_with_intervals_and_flags_recent_drift():
    deployment = am.import_report(_report(_history()))["deployment"]["deployment_id"]
    m = am.monitor(deployment, recent_days=7, baseline_days=28, now=START + timedelta(days=29))
    assert m["overall"]["runs"] == 61 and m["overall"]["by_status"]["running"] == 1
    assert m["overall"]["success"]["of"] == 60  # running runs are not finished
    assert m["recent"]["success"]["rate"] == 0.5 and m["baseline"]["success"]["rate"] == 1.0
    assert m["baseline"]["success"]["ci95"][0] > 0.9 and m["baseline"]["success"]["ci95"][1] == 1.0
    codes = {a["code"] for a in m["alerts"]}
    assert codes == {"success_rate_down", "handoff_rate_up"}
    assert m["drift"]["failure_rate"]["ci_excludes_zero"] is False
    assert m["overall"]["outcome_reported_verified"]["of"] == 50
    assert m["reported_by_the_automation_not_verified_by_owg"] is True and m["execution_authorized"] is False
    validate = next(s for s in m["steps"] if s["step_id"] == "step_002")
    assert validate["handed_to_human"] == 10 and validate["median_seconds_since_previous_step"] == 120
    assert m["design_conformance"] is None


def test_stable_automation_raises_no_alerts():
    runs = [_run(i, "succeeded", i // 3) for i in range(90)]
    deployment = am.import_report(_report(runs))["deployment"]["deployment_id"]
    assert am.monitor(deployment, now=START + timedelta(days=30))["alerts"] == []


def test_reviewed_design_comparison_uses_the_starter_spec():
    spec = _spec()
    runs = [_run(i, "succeeded", 1, extra_step=i < 3) for i in range(10)] + [_run(50, "handed_to_human", 2)]
    deployment = am.import_report(_report(runs, workflow_spec_sha256=am.spec_digest(spec)))["deployment"]["deployment_id"]
    attached = am.attach_design(deployment, spec)
    assert attached["design_attached"] and attached["design_sha256"] == am.spec_digest(spec)
    c = am.monitor(deployment)["design_conformance"]
    # Three runs completed an extra step and the handed-over run stopped early: 7 follow the design exactly.
    assert c["runs_with_steps"] == 11 and c["runs_completing_every_design_step_in_order"] == 7
    assert c["unexpected_step_ids"] == [{"step_id": "step_x", "runs": 3}] and c["design_steps_never_completed"] == []
    with pytest.raises(am.MonitoringError, match="different workflow spec"):
        am.import_report(_report([_run(60, "succeeded", 3)], workflow_spec_sha256="0" * 64), deployment)
    with pytest.raises(am.MonitoringError):
        am.attach_design(deployment, {"format": "other"})


def test_reimporting_a_run_replaces_it_and_reports_append():
    deployment = am.import_report(_report([_run(1, "running", 0)]))["deployment"]["deployment_id"]
    am.import_report(_report([_run(1, "succeeded", 0), _run(2, "failed", 0)]), deployment)
    m = am.monitor(deployment)
    assert m["overall"]["runs"] == 2 and m["overall"]["by_status"]["running"] == 0
    assert m["overall"]["by_status"]["failed"] == 1
    assert next(s for s in m["steps"] if s["step_id"] == "step_001")["attempts"] == 2


def test_report_hash_is_pinned_before_any_design_is_attached():
    spec=_spec();digest=am.spec_digest(spec)
    deployment=am.import_report(_report([_run(1,'succeeded',0)],workflow_spec_sha256=digest))['deployment']
    key=deployment['deployment_id']
    assert deployment['reported_spec_sha256']==digest and not deployment['design_attached']
    other={**spec,'title':'A different spec'}
    with pytest.raises(am.MonitoringError,match='does not match'):
        am.attach_design(key,other)
    with pytest.raises(am.MonitoringError,match='different workflow spec'):
        am.import_report(_report([_run(2,'failed',1)],workflow_spec_sha256=am.spec_digest(other)),key)
    unchanged=am.list_deployments()['deployments'][0]
    assert unchanged['runs']==1 and not unchanged['design_attached'] and unchanged['reported_spec_sha256']==digest
    assert am.attach_design(key,spec)['design_sha256']==digest


def test_first_declared_hash_pins_an_existing_unhashed_deployment():
    key=am.import_report(_report([_run(1,'failed',0)]))['deployment']['deployment_id']
    assert am.list_deployments()['deployments'][0]['reported_spec_sha256'] is None
    digest=am.spec_digest(_spec())
    result=am.import_report(_report([_run(2,'succeeded',1)],workflow_spec_sha256=digest),key)
    assert result['deployment']['reported_spec_sha256']==digest
    # Missing metadata stays supported; it does not clear an existing declared pin.
    result=am.import_report(_report([_run(3,'failed',2)]),key)
    assert result['deployment']['reported_spec_sha256']==digest and result['deployment']['runs']==3


def test_design_first_import_cannot_set_a_conflicting_report_pin():
    key=am.import_report(_report([_run(1,'failed',0)]))['deployment']['deployment_id']
    am.attach_design(key,_spec())
    with pytest.raises(am.MonitoringError):
        am.import_report(_report([_run(2,'succeeded',1)],workflow_spec_sha256='0'*64),key)
    info=am.list_deployments()['deployments'][0]
    assert info['reported_spec_sha256'] is None and info['runs']==1


def test_failed_import_rolls_back_a_new_spec_pin(monkeypatch):
    key=am.import_report(_report([_run(1,'failed',0)]))['deployment']['deployment_id']
    def failed(*args):raise RuntimeError('owned storage failure')
    monkeypatch.setattr(am,'_bulk_load',failed)
    with pytest.raises(RuntimeError):
        am.import_report(_report([_run(2,'succeeded',1)],workflow_spec_sha256=am.spec_digest(_spec())),key)
    info=am.list_deployments()['deployments'][0]
    assert info['reported_spec_sha256'] is None and info['runs']==1


def test_old_database_migrates_idempotently_without_inventing_a_pin():
    key=am.import_report(_report([_run(1,'succeeded',0)]))['deployment']['deployment_id']
    conn=am._connect()
    try:conn.execute('ALTER TABLE deployments DROP COLUMN reported_spec_sha256')
    finally:conn.close()
    for _ in range(2):
        info=am.list_deployments()['deployments'][0]
        assert info['deployment_id']==key and info['reported_spec_sha256'] is None and info['runs']==1
    assert am.monitor(key)['overall']['runs']==1


def test_nonfinite_attached_spec_is_a_validation_error():
    key=am.import_report(_report([_run(1,'failed',0)]))['deployment']['deployment_id']
    with pytest.raises(am.MonitoringError,match='finite JSON'):
        am.attach_design(key,{**_spec(),'invalid':float('nan')})


@pytest.mark.parametrize("change", [
    {"format": "x"}, {"extra": 1}, {"runs": []}, {"deployment": {"runtime": "zapier"}},
    {"deployment": {"workflow_spec_sha256": "ABC"}},
    {"runs": [{"run_id": "", "started_at": "2024-01-01T00:00:00Z", "status": "succeeded"}]},
    {"runs": [{"run_id": "a", "started_at": "2024-01-01T00:00:00", "status": "succeeded"}]},
    {"runs": [{"run_id": "a", "started_at": "2024-01-01T00:00:00Z", "status": "done"}]},
    {"runs": [{"run_id": "a", "started_at": "2024-01-02T00:00:00Z", "finished_at": "2024-01-01T00:00:00Z", "status": "failed"}]},
    {"runs": [{"run_id": "a", "started_at": "2024-01-01T00:00:00Z", "finished_at": "2024-01-01T01:00:00Z", "status": "running"}]},
    {"runs": [{"run_id": "a", "started_at": "2024-01-01T00:00:00Z", "status": "failed"}] * 2},
    {"runs": [{"run_id": "a", "started_at": "2024-01-01T00:00:00Z", "status": "failed", "outcome": {"verified": "yes"}}]},
    {"runs": [{"run_id": "a", "started_at": "2024-01-01T00:00:00Z", "status": "failed",
               "steps": [{"step_id": "s", "activity": "A", "status": "maybe", "at": "2024-01-01T00:00:00Z"}]}]},
    {"runs": [{"run_id": "a", "started_at": "2024-01-01T00:00:00Z", "status": "failed",
               "steps": [{"step_id": "s", "activity": "A", "status": "failed", "at": "2024-01-01T00:00:00Z"}] * 101}]},
])
def test_invalid_reports_are_refused_without_storing_anything(change):
    document = {**_report([_run(1, "succeeded", 0)]), **change}
    with pytest.raises(am.MonitoringError):
        am.import_report(document)
    assert am.list_deployments()["deployments"] == []


def test_delete_and_unknown_deployments(monkeypatch):
    deployment = am.import_report(_report([_run(1, "succeeded", 0)]))["deployment"]["deployment_id"]
    am.delete_deployment(deployment)
    assert am.list_deployments()["deployments"] == []
    for bad in (deployment, "dep_../x", "'; drop table runs; --"):
        with pytest.raises(am.MonitoringError):
            am.monitor(bad)
    monkeypatch.setattr(am, "MAX_DEPLOYMENTS", 1)
    am.import_report(_report([_run(1, "succeeded", 0)]))
    with pytest.raises(am.MonitoringError, match="delete"):
        am.import_report(_report([_run(2, "succeeded", 0)]))
    if os.name != "nt":
        assert am.database_path().stat().st_mode & 0o077 == 0


def test_runs_become_an_event_log_for_full_analytics():
    deployment = am.import_report(_report(_history()))["deployment"]["deployment_id"]
    data = am.runs_csv(deployment).decode().splitlines()
    assert data[0] == "run,step,time,status" and len(data) == 1 + 40 * 3 + 10 * 3 + 10 * 2 + 2
    log = workspace.import_log(BytesIO(am.runs_csv(deployment)), name="runs", source_format="csv", mapping={
        "case_column": "run", "activity_column": "step", "timestamp_column": "time", "resource_column": "status"})
    paths = {tuple(v["path"]): v["cases"] for v in workspace.variants(log["log_id"])["variants"]}
    assert paths[("step_001 Receive", "step_002 Validate", "step_003 Close")] == 50


def test_real_http_routes(tmp_path):
    code = r'''
import json
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
from tests.test_automation_monitoring import _report, _history

body = json.dumps(_report(_history())).encode()
with TestClient(app) as client:
    assert client.post("/v1/automation-monitoring/reports", content=body, headers={"Content-Type": "application/json"}).status_code == 401
    headers = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session(), "Content-Type": "application/json"}
    assert client.post("/v1/automation-monitoring/reports", content=body, headers=headers).status_code == 403
    confirmed = {**headers, "x-owg-local-storage-confirmed": "true"}
    made = client.post("/v1/automation-monitoring/reports", content=body, headers=confirmed)
    assert made.status_code == 200 and made.json()["runs_imported"] == 61, made.text
    dep = made.json()["deployment"]["deployment_id"]
    m = client.get(f"/v1/automation-monitoring/deployments/{dep}/monitor?recent_days=7&baseline_days=28", headers=headers)
    assert m.status_code == 200 and {a["code"] for a in m.json()["alerts"]} == {"success_rate_down", "handoff_rate_up"}, m.text
    assert m.headers["cache-control"] == "no-store"
    assert client.get(f"/v1/automation-monitoring/deployments/{dep}/monitor?recent_days=0", headers=headers).status_code == 422
    assert client.put(f"/v1/automation-monitoring/deployments/{dep}/design", json={"format": "x"}, headers=headers).status_code == 422
    assert client.post("/v1/automation-monitoring/reports", content=b"x", headers={**confirmed, "Content-Type": "text/plain"}).status_code == 415
    assert client.post("/v1/automation-monitoring/reports", content=b"{", headers=confirmed).status_code == 422
    assert client.post(f"/v1/automation-monitoring/deployments/{dep}/event-log", headers=headers).status_code == 403
    log = client.post(f"/v1/automation-monitoring/deployments/{dep}/event-log", headers=confirmed)
    assert log.status_code == 200 and log.json()["name"] == "Refund agent runs", log.text
    assert client.post(f"/v1/automation-monitoring/deployments/{dep}/delete", headers=headers).status_code == 200
    assert client.get("/v1/automation-monitoring/deployments", headers=headers).json()["deployments"] == []
    assert client.get("/automation-monitoring.js").status_code == 200
'''
    env = os.environ.copy()
    env.update({
        "WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"),
        "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
        "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json"),
        "PYTHONPATH": str(ROOT),
    })
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"


def test_dashboard_client_helpers():
    html = (ROOT / "dashboard/index.html").read_text(encoding="utf-8")
    script = (ROOT / "dashboard/automation_monitoring.js").read_text(encoding="utf-8")
    assert '<script src="/automation-monitoring.js"></script>' in html
    assert "innerHTML" not in script and "localStorage" not in script
    import shutil
    if not shutil.which("node"):
        return
    check = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const scope={window:{},document:{}};
vm.runInNewContext(fs.readFileSync('dashboard/automation_monitoring.js','utf8'),scope);
const m=scope.window.__owgMonitorModel;
assert.equal(m.rate({rate:0.5,ci95:[0.3,0.7],of:20}),'50.0% [30.0%, 70.0%] of 20');
assert.equal(m.rate(null),'—');
assert.equal(m.change({difference:-0.25,ci95:[-0.4,-0.1]}),'-25.0 pts [-40.0, -10.0]');
assert.equal(m.change(null),'not enough runs');
assert.equal(m.checkReport({format:'openworkgraph.automation-run-report.v1',runs:[{}]}),1);
assert.throws(()=>m.checkReport({format:'x'}));assert.throws(()=>m.checkReport({format:'openworkgraph.automation-run-report.v1',runs:[]}));
"""
    result = subprocess.run(["node", "-e", check], cwd=ROOT, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
