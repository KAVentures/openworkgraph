"""Local automation opportunity pipeline (roadmap #326 F4)."""
from __future__ import annotations

import csv
import io
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import automation_pipeline as pipeline

ROOT = Path(__file__).resolve().parents[1]
ALL_FIVE = {q: 5 for q in pipeline.EASE_QUESTIONS}


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "DATA_DIR", tmp_path)


def _assessment(revision, **changes):
    return {"ease": ALL_FIVE, "cases_per_month": 300, "minutes_per_case": 10, "eligible_share": 0.5,
            "expected_revision": revision, **changes}


def test_full_lifecycle_records_history_and_never_authorizes_execution():
    idea = pipeline.create({"title": "Copy approved prices", "source": {"kind": "process_catalog", "reference": "renewals r3"}})
    assert idea["state"] == "idea" and idea["revision"] == 1 and idea["execution_authorized"] is False
    assert idea["allowed_transitions"] == ["assessing", "on_hold", "rejected"]
    idea = pipeline.transition(idea["idea_id"], {"to": "assessing", "expected_revision": 1})
    with pytest.raises(pipeline.PipelineError, match="assess the idea"):
        pipeline.transition(idea["idea_id"], {"to": "approved", "expected_revision": idea["revision"]})
    idea = pipeline.assess(idea["idea_id"], _assessment(idea["revision"]))
    assert idea["score"] == {"ease_score": 100, "modeled_hours_per_month": 25.0, "quadrant": "quick_win",
                             "thresholds": {"ease": 60, "modeled_hours_per_month": 20},
                             "hours_are_modeled_from_entered_assumptions": True, "savings_claimed": False}
    idea = pipeline.transition(idea["idea_id"], {"to": "approved", "note": "Owner agreed", "expected_revision": idea["revision"]})
    idea = pipeline.transition(idea["idea_id"], {"to": "implemented", "expected_revision": idea["revision"]})
    assert [h["state"] for h in idea["history"] if h.get("action") != "assessment_saved"] == ["idea", "assessing", "approved", "implemented"]
    assert idea["history"][3]["note"] == "Owner agreed" and idea["allowed_transitions"] == []
    with pytest.raises(pipeline.PipelineError, match="cannot be reassessed"):
        pipeline.assess(idea["idea_id"], _assessment(idea["revision"]))
    listing = pipeline.pipeline()
    assert listing["approval_authorizes_execution"] is False and listing["counts"]["implemented"] == 1


def test_concurrent_edits_are_refused_with_the_current_revision():
    idea = pipeline.create({"title": "Triage tickets"})
    pipeline.transition(idea["idea_id"], {"to": "assessing", "expected_revision": 1})
    with pytest.raises(pipeline.PipelineError, match="changed .*revision 2"):
        pipeline.transition(idea["idea_id"], {"to": "on_hold", "expected_revision": 1})
    with pytest.raises(pipeline.PipelineError, match="changed"):
        pipeline.assess(idea["idea_id"], _assessment(1))


@pytest.mark.parametrize("ease,hours,quadrant", [
    (ALL_FIVE, (300, 10, 0.5), "quick_win"),
    ({q: 1 for q in pipeline.EASE_QUESTIONS}, (300, 10, 0.5), "major_project"),
    (ALL_FIVE, (10, 5, 1), "fill_in"),
    ({q: 2 for q in pipeline.EASE_QUESTIONS}, (10, 5, 1), "deprioritize"),
])
def test_quadrants_follow_stated_thresholds(ease, hours, quadrant):
    cases, minutes, share = hours
    result = pipeline.score({"ease": ease, "cases_per_month": cases, "minutes_per_case": minutes, "eligible_share": share})
    assert result["quadrant"] == quadrant


@pytest.mark.parametrize("payload,message", [
    ({"title": ""}, "title"),
    ({"title": "   "}, "blank"),
    ({"title": "x", "owner": "me"}, "Extra inputs"),
    ({"title": "x", "source": {"kind": "screen_recording"}}, "source"),
])
def test_bad_ideas_are_refused(payload, message):
    with pytest.raises(pipeline.PipelineError, match=message):
        pipeline.create(payload)


@pytest.mark.parametrize("changes,message", [
    ({"ease": {"rules_based": 5}}, "every ease question"),
    ({"ease": {**ALL_FIVE, "rules_based": 6}}, "less than or equal"),
    ({"eligible_share": 1.5}, "less than or equal"),
    ({"cases_per_month": -1}, "greater than or equal"),
    ({"estimated_savings": 1000}, "Extra inputs"),
])
def test_bad_assessments_are_refused(changes, message):
    idea = pipeline.create({"title": "x"})
    with pytest.raises(pipeline.PipelineError, match=message):
        pipeline.assess(idea["idea_id"], _assessment(1, **changes))


def test_transition_rules_and_reopening_rejected_ideas():
    idea = pipeline.create({"title": "x"})
    with pytest.raises(pipeline.PipelineError, match="cannot move from idea to implemented"):
        pipeline.transition(idea["idea_id"], {"to": "implemented", "expected_revision": 1})
    idea = pipeline.transition(idea["idea_id"], {"to": "rejected", "note": "Not worth it", "expected_revision": 1})
    idea = pipeline.transition(idea["idea_id"], {"to": "idea", "expected_revision": idea["revision"]})
    assert idea["state"] == "idea" and len(idea["history"]) == 3
    with pytest.raises(pipeline.PipelineError, match="unknown idea"):
        pipeline.transition("idea_missing", {"to": "assessing", "expected_revision": 1})


def test_listing_orders_by_priority_and_filters_by_state():
    slow = pipeline.create({"title": "Slow"})
    pipeline.assess(slow["idea_id"], _assessment(1, ease={q: 1 for q in pipeline.EASE_QUESTIONS}))
    quick = pipeline.create({"title": "Quick"})
    pipeline.assess(quick["idea_id"], _assessment(1))
    pipeline.create({"title": "Unassessed"})
    assert [i["title"] for i in pipeline.pipeline()["ideas"]] == ["Quick", "Slow", "Unassessed"]
    assert pipeline.pipeline("idea")["counts"]["idea"] == 3
    with pytest.raises(pipeline.PipelineError):
        pipeline.pipeline("approved_by_ai")


def test_csv_export_guards_against_formula_injection_and_delete_removes():
    idea = pipeline.create({"title": "=HYPERLINK(\"http://x\")", "source": {"reference": "+cmd"}})
    rows = list(csv.reader(io.StringIO(pipeline.export_csv())))
    assert rows[0][0] == "idea_id" and rows[1][1].startswith("'=") and rows[1][4].startswith("'+")
    pipeline.delete(idea["idea_id"])
    assert pipeline.pipeline()["ideas"] == []
    with pytest.raises(pipeline.PipelineError):
        pipeline.delete(idea["idea_id"])


def test_store_is_bounded(monkeypatch):
    monkeypatch.setattr(pipeline, "MAX_IDEAS", 1)
    pipeline.create({"title": "one"})
    with pytest.raises(pipeline.PipelineError, match="at most"):
        pipeline.create({"title": "two"})


def test_real_http_routes_require_session_and_map_conflicts(tmp_path):
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
with TestClient(app) as client:
    headers = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    assert client.get("/v1/automation-pipeline", headers=bearer).status_code in (401, 403)
    assert client.post("/v1/automation-pipeline", json={"title": "x"}, headers=bearer).status_code in (401, 403)
    assert client.get("/automation-pipeline.js").status_code == 200
    made = client.post("/v1/automation-pipeline", json={"title": "Invoices"}, headers=headers)
    assert made.status_code == 200, made.text
    idea = made.json()["idea_id"]
    moved = client.post(f"/v1/automation-pipeline/{idea}/transition", json={"to": "assessing", "expected_revision": 1}, headers=headers)
    assert moved.status_code == 200
    stale = client.post(f"/v1/automation-pipeline/{idea}/transition", json={"to": "on_hold", "expected_revision": 1}, headers=headers)
    assert stale.status_code == 409
    assessment = {"ease": {q: 4 for q in ("rules_based", "structured_inputs", "process_stability", "digital_inputs", "low_exception_rate")},
                  "cases_per_month": 300, "minutes_per_case": 10, "eligible_share": .5, "expected_revision": 2}
    assessed = client.post(f"/v1/automation-pipeline/{idea}/assess", json=assessment, headers=headers).json()
    approved = client.post(f"/v1/automation-pipeline/{idea}/transition", json={"to": "approved", "expected_revision": assessed["revision"]}, headers=headers).json()
    reassessed = client.post(f"/v1/automation-pipeline/{idea}/assess", json={**assessment, "minutes_per_case": 1, "expected_revision": approved["revision"]}, headers=headers)
    assert reassessed.status_code == 200 and reassessed.json()["state"] == "assessing"
    assert reassessed.headers["cache-control"] == "no-store"
    cannot_implement = client.post(f"/v1/automation-pipeline/{idea}/transition", json={"to": "implemented", "expected_revision": reassessed.json()["revision"]}, headers=headers)
    assert cannot_implement.status_code == 422
    assert client.post(f"/v1/automation-pipeline/{idea}/assess", json=assessment, headers=bearer).status_code in (401, 403)
    assert client.get("/v1/automation-pipeline/idea_nope", headers=headers).status_code == 404
    assert client.post("/v1/automation-pipeline", content=b"{", headers={**headers, "content-type": "application/json"}).status_code == 422
    exported = client.get("/v1/automation-pipeline/export.csv", headers=headers)
    assert exported.status_code == 200 and "Invoices" in exported.text and exported.headers["cache-control"] == "no-store"
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"


def test_dashboard_assessment_helper():
    check = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const scope={window:{},document:{}};
vm.runInNewContext(fs.readFileSync('dashboard/automation_pipeline.js','utf8'),scope);
const m=scope.window.__owgPipelineModel;
const all={rules_based:'5',structured_inputs:'4',process_stability:'3',digital_inputs:'2',low_exception_rate:'1'};
const body=m.assessmentBody({...all,cases:'100',minutes:'6',share:'50',compliance:true},3);
assert.deepEqual(JSON.parse(JSON.stringify(body)),{ease:{rules_based:5,structured_inputs:4,process_stability:3,digital_inputs:2,low_exception_rate:1},cases_per_month:100,minutes_per_case:6,eligible_share:0.5,compliance_review_needed:true,expected_revision:3});
assert.throws(()=>m.assessmentBody({...all,rules_based:'',cases:'1',minutes:'1',share:'1'},1),/every ease question/);
assert.throws(()=>m.assessmentBody({...all,cases:'1',minutes:'1',share:'150'},1),/eligible share/);
"""
    result = subprocess.run(["node", "-e", check], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr


def test_reassessing_approved_idea_requires_fresh_review_and_keeps_assessment_basis():
    idea = pipeline.create({"title": "Review renewals"})
    idea = pipeline.transition(idea["idea_id"], {"to": "assessing", "expected_revision": idea["revision"]})
    idea = pipeline.assess(idea["idea_id"], _assessment(idea["revision"]))
    first_assessment = idea["assessment"]
    idea = pipeline.transition(idea["idea_id"], {"to": "approved", "expected_revision": idea["revision"]})
    approved_revision = idea["revision"]
    revised = pipeline.assess(idea["idea_id"], _assessment(approved_revision, minutes_per_case=1))
    assert revised["state"] == "assessing"
    assert revised["revision"] == approved_revision + 1
    assert revised["assessment"]["minutes_per_case"] == 1
    assert revised["score"]["modeled_hours_per_month"] == 2.5
    assert "implemented" not in revised["allowed_transitions"]
    event = revised["history"][-1]
    assert event["action"] == "assessment_saved" and event["approval_invalidated"] is True
    assert event["previous_assessment"] == first_assessment
    assert event["assessment"] == revised["assessment"]
    assert event["revision"] == revised["revision"]
    with pytest.raises(pipeline.PipelineError, match="changed"):
        pipeline.transition(idea["idea_id"], {"to": "implemented", "expected_revision": approved_revision})
    with pytest.raises(pipeline.PipelineError, match="cannot move"):
        pipeline.transition(idea["idea_id"], {"to": "implemented", "expected_revision": revised["revision"]})
    assert pipeline.get(idea["idea_id"]) == revised
    reapproved = pipeline.transition(idea["idea_id"], {"to": "approved", "expected_revision": revised["revision"]})
    assert reapproved["history"][-1]["assessment"] == revised["assessment"]
    assert reapproved["execution_authorized"] is False


def test_reassessment_failure_rolls_back_history_and_approval(monkeypatch):
    idea = pipeline.create({"title": "Invoices"})
    idea = pipeline.transition(idea["idea_id"], {"to": "assessing", "expected_revision": 1})
    idea = pipeline.assess(idea["idea_id"], _assessment(idea["revision"]))
    idea = pipeline.transition(idea["idea_id"], {"to": "approved", "expected_revision": idea["revision"]})
    real_public = pipeline._public
    def fail_response(row):
        raise RuntimeError("synthetic response failure")
    monkeypatch.setattr(pipeline, "_public", fail_response)
    with pytest.raises(RuntimeError, match="synthetic response failure"):
        pipeline.assess(idea["idea_id"], _assessment(idea["revision"], minutes_per_case=1))
    monkeypatch.setattr(pipeline, "_public", real_public)
    assert pipeline.get(idea["idea_id"]) == idea


def test_legacy_approved_assessment_is_preserved_on_resubmission():
    import json
    idea = pipeline.create({"title": "Legacy opportunity"})
    original = _assessment(1)
    original.pop("expected_revision")
    # Old rows recorded state transitions but no historical assessment basis.
    with pipeline._connect() as conn:
        conn.execute("UPDATE ideas SET state = 'approved', assessment = ? WHERE idea_id = ?",
                     [json.dumps(original), idea["idea_id"]])
    current = pipeline.get(idea["idea_id"])
    assert current["state"] == "approved" and "assessment" not in current["history"][0]
    revised = pipeline.assess(idea["idea_id"], _assessment(1, eligible_share=0.1))
    assert revised["state"] == "assessing"
    assert revised["history"][-1]["previous_assessment"] == original
    assert "assessment" not in revised["history"][0]  # no fabricated old approval basis


def test_assessment_history_remains_bounded_and_survives_reopen():
    idea = pipeline.create({"title": "Frequently revised"})
    for i in range(105):
        idea = pipeline.assess(idea["idea_id"], _assessment(idea["revision"], cases_per_month=i))
    reopened = pipeline.get(idea["idea_id"])
    assert len(reopened["history"]) == 100
    assert reopened["history"][0]["assessment"]["cases_per_month"] == 5
    assert reopened["history"][-1]["assessment"]["cases_per_month"] == 104
    assert reopened["history"][-1]["approval_invalidated"] is False
    assert reopened["state"] == "idea" and reopened["revision"] == 106
