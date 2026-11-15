"""R16: one portfolio-wide human data-quality worklist, never an ROI claim."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import automation_pipeline as pipeline
from server import automation_benefits as benefits
from server import automation_benefit_readiness as readiness

TODAY = datetime.now(timezone.utc).date()
BEFORE = TODAY - timedelta(days=1)
EASE = {key: 5 for key in pipeline.EASE_QUESTIONS}


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "DATA_DIR", tmp_path)


def implemented(title):
    idea = pipeline.create({"title": title})
    idea = pipeline.transition(idea["idea_id"], {"to": "assessing",
                                               "expected_revision": idea["revision"]})
    idea = pipeline.assess(idea["idea_id"], {
        "ease": EASE, "cases_per_month": 100, "minutes_per_case": 10,
        "eligible_share": 1, "expected_revision": idea["revision"]})
    idea = pipeline.transition(idea["idea_id"], {"to": "approved",
                                               "expected_revision": idea["revision"]})
    idea = pipeline.transition(idea["idea_id"], {"to": "implemented",
                                               "expected_revision": idea["revision"]})
    return idea["idea_id"]


def record(idea_id, period, *, minutes=10, cases=20, ref="typed-source", source="system_report"):
    day = BEFORE if period == "baseline" else TODAY
    return benefits.record(idea_id, {
        "period": period, "start": day.isoformat(), "end": day.isoformat(),
        "cases": cases, "minutes_per_case": minutes,
        "source": source, "source_reference": ref})


def by_id(review):
    return {row["idea_id"]: row for row in review["ideas"]}


def test_empty_portfolio_does_not_claim_value_or_scan_external_sources():
    report = readiness.review()
    assert report["ideas"] == [] and report["implemented_ideas_reviewed"] == 0
    assert report["candidate_overlap_pairs"] == 0
    assert report["comparison_complete"] is True
    assert report["portfolio_savings_summed"] is False
    assert report["source_reference_values_returned"] is False
    assert report["execution_authorized"] is False


def test_actionable_missing_baseline_after_zero_denominator_and_provenance():
    missing = implemented("No measurements")
    after_missing = implemented("Only baseline")
    zero = implemented("Zero case denominator")
    unlinked = implemented("Unlinked source")
    complete = implemented("Reference ready")
    record(after_missing, "baseline")
    record(zero, "baseline", cases=0)
    record(zero, "after", cases=15)
    record(unlinked, "baseline", ref="")
    record(unlinked, "after", ref="")
    record(complete, "baseline", minutes=20, ref="baseline-unique")
    record(complete, "after", minutes=10, ref="after-unique")
    r = readiness.review()
    states = by_id(r)
    assert states[missing]["readiness"] == "missing_baseline"
    assert states[after_missing]["readiness"] == "missing_after"
    assert states[zero]["readiness"] == "zero_case_period"
    assert states[unlinked]["readiness"] == "missing_source_reference"
    assert states[unlinked]["missing_source_references"] == 2
    assert states[complete]["readiness"] == "ready_for_noncausal_comparison"
    assert states[complete]["reported_effort_change_hours_per_month_at_after_volume"] is not None
    for row in states.values():
        assert row["causal_effect_verified"] is False
        assert row["reported_change_is_savings"] is False
    serialized = json.dumps(r)
    assert "baseline-unique" not in serialized and "after-unique" not in serialized
    assert "typed-source" not in serialized
    assert r["portfolio_savings_summed"] is False
    assert r["realized_savings_verified"] is False


def test_cross_idea_source_overlap_suppresses_effort_comparison():
    a, b = implemented("Returns"), implemented("Refunds")
    for idea_id in (a,b):
        record(idea_id,"baseline",minutes=30,ref="baseline-"+idea_id)
        record(idea_id,"after",minutes=12,ref="shared-after")
    result = readiness.review()
    assert result["candidate_overlap_pairs"] == 1
    for idea_id in (a,b):
        item = by_id(result)[idea_id]
        assert item["readiness"] == "cross_idea_overlap_candidate"
        assert item["cross_idea_overlap_candidate"] is True
        assert item["reported_effort_change_hours_per_month_at_after_volume"] is None
    assert "shared-after" not in json.dumps(result)


def test_corrupted_stored_evidence_never_reads_as_complete():
    idea_id = implemented("With corrupted legacy row")
    record(idea_id,"baseline")
    record(idea_id,"after")
    with closing(pipeline._connect()) as conn, conn:
        conn.execute("UPDATE idea_measurements SET body=? WHERE idea_id=? AND body LIKE '%after%'",
                     ('{not-valid-json',idea_id))
    r=readiness.review()
    assert by_id(r)[idea_id]["readiness"] == "unreadable_evidence"
    assert r["comparison_complete"] is False
    assert r["unreadable_measurements_in_overlap_scan"] >= 1


def test_unimplemented_ideas_never_enter_review():
    x=pipeline.create({"title":"Unapproved automation"})
    y=implemented("Implemented")
    result=readiness.review()
    assert result["implemented_ideas_reviewed"]==1
    assert by_id(result).keys()=={y}
    assert x["idea_id"] not in by_id(result)


def test_authenticated_local_http_and_no_cache(tmp_path):
    code = r"""
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
route='/v1/automation-pipeline/benefit-readiness'
with TestClient(app) as client:
    assert client.get(route).status_code==401
    bearer={'Authorization':'Bearer '+local_auth.ensure_api_token()}
    assert client.get(route,headers=bearer).status_code in (401,403)
    session={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
    r=client.get(route,headers=session)
    assert r.status_code==200,r.text
    assert r.headers['cache-control']=='no-store'
    data=r.json()
    assert data['format']=='openworkgraph.automation-portfolio-benefit-readiness.v1'
    assert data['ideas']==[]
    assert data['portfolio_savings_summed'] is False
    assert data['execution_authorized'] is False
"""
    env=os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA":str(tmp_path/"api"),
                "WORKFLOW_OBSERVER_AUTH_DIR":str(tmp_path/"auth"),
                "WORKFLOW_OBSERVER_CONFIG":str(tmp_path/"config.json")})
    run=subprocess.run([sys.executable,"-c",code],cwd=Path(__file__).resolve().parents[1],
                       env=env,capture_output=True,text=True,timeout=120)
    assert run.returncode==0, f"stdout={run.stdout}\nstderr={run.stderr}"


def test_dashboard_has_bounded_filterable_readiness_worklist():
    path=Path(__file__).resolve().parents[1]/"dashboard"/"automation_pipeline.js"
    script=path.read_text(encoding="utf-8")
    assert "/v1/automation-pipeline/benefit-readiness" in script
    assert "owgPipelineBenefitReadinessResult" in script
    assert "owgPipelineBenefitReadinessFilter" in script
    assert "matched.slice(0,150)" in script
