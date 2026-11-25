"""Exact incident populations and stale-evidence refusal, using retained runs."""
from copy import deepcopy
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import automation_incidents as incidents
from server import automation_incident_evidence as review
from server import automation_monitoring as m
from server import automation_run_evidence as evidence
from test_automation_run_evidence import report, run


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "DATA_DIR", tmp_path)
    monkeypatch.setattr(incidents, "DATA_DIR", tmp_path)


def create(runs):
    dep = m.import_report(report(runs))["deployment"]["deployment_id"]
    incidents.reconcile_deployment(dep)
    return dep


def incident(dep, fingerprint="step:Check", kind="run_failures"):
    return next(i for i in incidents.listing(deployment_id=dep)["incidents"]
                if i["kind"] == kind and i["fingerprint"] == fingerprint)


def spec(i, **kwargs):
    return {"expected_last_seen": i["last_seen"],
            "expected_review_revision": i["review_revision"],
            "expected_membership_sha256": i["run_membership_sha256"], **kwargs}


def test_full_population_first_problem_not_any_failed_step_and_deployment_isolation():
    rows = [run("f%03d" % i) for i in range(46)]
    rows += [run("other", steps=[("x", "Other", "failed"), ("check", "Check", "failed")]),
             run("ok", "succeeded", []), run("no-step", steps=[])]
    dep = create(rows)
    # Different name establishes independent deployment even with identical run IDs.
    doc = report([run("f000")]);doc["deployment"]["name"] = "Other team"
    other = m.import_report(doc)["deployment"]["deployment_id"]
    assert other != dep
    incidents.reconcile_deployment(other)
    item = incident(dep)
    assert len(item["sample_refs"]) == 10
    first = review.review(item["incident_id"], spec(item, limit=7))
    assert first["matching_runs"] == 46 and first["matching_steps"] == 92
    seen = [r["run_id"] for r in first["rows"]]
    page = first
    while page["next_offset"] is not None:
        page = review.review(item["incident_id"], spec(item, limit=7, offset=page["next_offset"],
                            expected_population_sha256=first["population_sha256"]))
        seen.extend(r["run_id"] for r in page["rows"])
    assert seen == ["f%03d" % i for i in range(46)]
    ref = first["rows"][0]["reference"]
    assert evidence.detail(dep, ref["run_id"], ref["run_sha256"])["reference"] == ref
    assert first["membership_sha256"] == item["run_membership_sha256"]
    assert not first["execution_authorized"] and not first["business_outcomes_verified"]
    other_item = incident(dep, "step:Other")
    assert review.review(other_item["incident_id"], spec(other_item))["matching_runs"] == 1
    none = incident(dep, "no_step_reported")
    assert review.review(none["incident_id"], spec(none))["rows"][0]["first_reported_issue"] is None


def test_step_content_changes_beyond_samples_invalidate_population():
    dep = create([run("f%02d" % i) for i in range(15)])
    item = incident(dep)
    first = review.review(item["incident_id"], spec(item, limit=1))
    modified = run("f14")
    modified["steps"][0]["activity"] = "Changed earlier step"
    m.import_report(report([modified]), dep)
    assert incidents.reconcile_deployment(dep) == []
    with pytest.raises(m.MonitoringError, match="evidence changed"):
        review.review(item["incident_id"], spec(item, offset=1,
                      expected_population_sha256=first["population_sha256"]))
    assert review.review(item["incident_id"], spec(item))["population_sha256"] != first["population_sha256"]


def test_unrelated_run_does_not_invalidate_population_and_unreconciled_membership_refused():
    dep = create([run("a"), run("b")])
    item = incident(dep)
    first = review.review(item["incident_id"], spec(item))
    m.import_report(report([run("ok", "succeeded", [])]), dep)
    assert review.review(item["incident_id"], spec(item))["population_sha256"] == first["population_sha256"]
    m.import_report(report([run("c")]), dep)
    with pytest.raises(m.MonitoringError, match="reconcile"):
        review.review(item["incident_id"], spec(item))
    incidents.reconcile_deployment(dep)
    with pytest.raises(m.MonitoringError, match="incident changed"):
        review.review(item["incident_id"], spec(item))


def test_review_revision_and_zero_current_membership():
    dep = create([run("a")])
    item = incident(dep)
    incidents.triage(item["incident_id"], {"status":"resolved", "expected_last_seen":item["last_seen"],
                                         "expected_review_revision":item["review_revision"]})
    with pytest.raises(m.MonitoringError, match="incident changed"):
        review.review(item["incident_id"], spec(item))
    m.import_report(report([run("a", "succeeded", [])]), dep)
    incidents.reconcile_deployment(dep)
    current = incident(dep)
    zero = review.review(current["incident_id"], spec(current))
    assert zero["rows"] == [] and zero["matching_runs"] == 0 and zero["next_offset"] is None


def test_handoff_and_channel_occurrences_never_mixed():
    dep = create([run("h", "handed_to_human", [("x", "Check", "handed_to_human")]), run("f")])
    handoff = incident(dep, kind="run_handoffs")
    assert review.review(handoff["incident_id"], spec(handoff))["rows"][0]["run_id"] == "h"
    channel = incidents.record(dep, "signature_rejected", "test")
    with pytest.raises(m.MonitoringError, match="channel occurrences"):
        review.review(channel["incident_id"], spec(handoff))


def test_bounds_refuse_partial_population_and_strict_selection(monkeypatch):
    dep = create([run("a"), run("b")])
    item = incident(dep)
    with pytest.raises(m.MonitoringError, match="page one"):
        review.review(item["incident_id"], spec(item, offset=1))
    for invalid in ({"limit": True}, {"limit":101}, {"offset":-1}, {"unexpected":0}):
        with pytest.raises(m.MonitoringError):
            review.review(item["incident_id"], spec(item, **invalid))
    monkeypatch.setattr(review, "MAX_RUNS", 1)
    with pytest.raises(m.MonitoringError, match="run evidence limit"):
        review.review(item["incident_id"], spec(item))
    monkeypatch.setattr(review, "MAX_RUNS", 10)
    monkeypatch.setattr(review, "MAX_STEPS", 1)
    with pytest.raises(m.MonitoringError, match="step evidence limit"):
        review.review(item["incident_id"], spec(item))


def test_http_session_only_strict_json_and_no_store(tmp_path):
    code = r"""
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
from test_automation_run_evidence import report, run
from test_automation_incident_evidence import spec
with TestClient(app) as client:
    h={"Authorization":"OWG-Session "+local_auth.create_dashboard_session()}
    stored={**h,"X-OWG-Local-Storage-Confirmed":"true"}
    bearer={"Authorization":"Bearer "+local_auth.ensure_api_token()}
    made=client.post("/v1/automation-monitoring/reports",json=report([run("a"),run("b")]),headers=stored)
    assert made.status_code==200,made.text
    item=client.get("/v1/automation-monitoring/incidents",headers=h).json()["incidents"][0]
    url="/v1/automation-monitoring/incidents/"+item["incident_id"]+"/evidence"
    assert client.post(url,json=spec(item),headers=bearer).status_code in (401,403)
    ok=client.post(url,json=spec(item),headers=h)
    assert ok.status_code==200,ok.text
    assert ok.headers["cache-control"]=="no-store"
    assert ok.json()["matching_runs"]==2
    decision=spec(item,status="acknowledged",note="Reviewed",
                  expected_population_sha256=ok.json()["population_sha256"])
    assert client.post(url+"/triage",json=decision,headers=bearer).status_code in (401,403)
    saved=client.post(url+"/triage",json=decision,headers=h)
    assert saved.status_code==200,saved.text
    assert saved.headers["cache-control"]=="no-store"
    assert saved.json()["incident"]["status"]=="acknowledged"
    assert client.post(url+"/triage",json=decision,headers=h).status_code==422
    assert client.post(url,content=b'{"limit":1,"limit":2}',headers={**h,"Content-Type":"application/json"}).status_code==422
"""
    env={**os.environ,"WORKFLOW_OBSERVER_DATA":str(tmp_path/"data"),
         "WORKFLOW_OBSERVER_AUTH_DIR":str(tmp_path/"auth"),
         "WORKFLOW_OBSERVER_CONFIG":str(tmp_path/"config.json")}
    p=subprocess.run([sys.executable,"-c","import sys;sys.path.insert(0,'tests');"+code],
                     cwd=Path(__file__).resolve().parents[1],env=env,capture_output=True,text=True,timeout=120)
    assert p.returncode==0,p.stdout+p.stderr


def test_pinned_triage_refuses_content_change_even_when_ordinary_incident_pin_is_current():
    dep = create([run("a"), run("b")])
    item = incident(dep)
    first = review.review(item["incident_id"], spec(item))
    payload = spec(item, expected_population_sha256=first["population_sha256"],
                   status="resolved", note="Reviewed exact population")
    modified = run("b")
    modified["steps"][0]["activity"] = "Different earlier context"
    m.import_report(report([modified]), dep)
    assert incidents.reconcile_deployment(dep) == []
    with pytest.raises(m.MonitoringError, match="evidence changed"):
        review.triage_reviewed(item["incident_id"], payload)
    assert incident(dep)["status"] == "open"
    assert incident(dep)["notes"] == []
    fresh = review.review(item["incident_id"], spec(item))
    result = review.triage_reviewed(item["incident_id"],
        {**payload, "expected_population_sha256": fresh["population_sha256"]})
    assert result["incident"]["status"] == "resolved"
    assert result["reviewed_population_sha256"] == fresh["population_sha256"]
    assert result["reviewed_run_count"] == 2
    assert result["human_reading_independently_verified"] is False
    assert not result["business_writes_replayed"]
    with pytest.raises(m.MonitoringError, match="incident changed"):
        review.triage_reviewed(item["incident_id"], {**payload, "expected_population_sha256": fresh["population_sha256"]})


def test_pinned_triage_requires_exact_digest_and_rejects_extra_fields():
    dep = create([run("a")])
    item = incident(dep)
    first = review.review(item["incident_id"], spec(item))
    for bad in (spec(item, status="resolved"),
                spec(item, status="resolved", expected_population_sha256="bad"),
                spec(item, status="resolved", expected_population_sha256=first["population_sha256"], execute=True)):
        with pytest.raises(m.MonitoringError):
            review.triage_reviewed(item["incident_id"], bad)
    assert incident(dep)["status"] == "open"
