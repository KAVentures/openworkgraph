"""R15 local portfolio dependency schedule review: declared facts, no deadline forecast."""
from __future__ import annotations

from contextlib import closing
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import automation_dependency_schedule as review
from server import automation_pipeline as pipeline

ALL_FIVE = {name: 5 for name in pipeline.EASE_QUESTIONS}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "DATA_DIR", tmp_path)


def create(title: str, quarter: str = "", dependencies: list[str] | None = None):
    idea = pipeline.create({"title": title})
    if quarter or dependencies:
        idea = pipeline.plan(idea["idea_id"], {
            "target_quarter": quarter, "depends_on": dependencies or [],
            "expected_revision": idea["revision"]})
    return idea


def implement(idea: dict):
    idea = pipeline.transition(idea["idea_id"], {"to": "assessing", "expected_revision": idea["revision"]})
    idea = pipeline.assess(idea["idea_id"], {"ease": ALL_FIVE, "cases_per_month": 10,
        "minutes_per_case": 30, "eligible_share": 1, "expected_revision": idea["revision"]})
    idea = pipeline.transition(idea["idea_id"], {"to": "approved", "expected_revision": idea["revision"]})
    return pipeline.transition(idea["idea_id"], {"to": "implemented", "expected_revision": idea["revision"]})


def test_finds_direct_and_transitive_plan_conflicts_with_paths():
    foundation = create("Foundation", "2027-Q3")
    middle = create("Middle", "2027-Q2", [foundation["idea_id"]])
    early = create("Early target", "2027-Q1", [middle["idea_id"]])
    result = review.review()
    assert result["active_ideas"] == 3
    assert result["active_ideas_with_dependencies"] == 2
    issues = [r for r in result["risk_candidates"] if r["kind"] == "dependency_target_after_dependent"]
    assert len(issues) == 3  # Middle -> Foundation, Early -> Middle and Foundation.
    assert any(r["idea_id"] == early["idea_id"] and r["dependency_id"] == foundation["idea_id"]
               and r["path"] == [early["idea_id"], middle["idea_id"], foundation["idea_id"]] for r in issues)
    assert result["schedule_feasibility_verified"] is False
    assert result["source_systems_queried"] is False


def test_implemented_dependency_no_longer_blocks_review():
    foundation = create("Done", "2028-Q4")
    child = create("Not yet", "2027-Q1", [foundation["idea_id"]])
    assert any(x["dependency_id"] == foundation["idea_id"] for x in review.review()["risk_candidates"])
    implement(foundation)
    result = review.review()
    assert result["risk_candidates"] == []
    assert result["status"] == "no_declared_conflicts_detected"
    assert result["active_ideas"] == 1


def test_rejected_and_unscheduled_dependencies_require_review():
    foundation = create("Undated")
    child = create("Promised", "2027-Q2", [foundation["idea_id"]])
    review_before = review.review()
    assert any(r["kind"] == "unscheduled_dependency" and r["idea_id"] == child["idea_id"]
               for r in review_before["risk_candidates"])
    pipeline.transition(foundation["idea_id"], {"to": "rejected", "expected_revision": foundation["revision"]})
    result = review.review()
    kinds = {r["kind"] for r in result["risk_candidates"] if r["idea_id"] == child["idea_id"]}
    assert {"blocked_dependency", "unscheduled_dependency"} <= kinds


def test_old_missing_dependency_is_disclosed_not_silently_ignored():
    child = create("Legacy", "2027-Q1")
    with closing(pipeline._connect()) as conn, conn:
        raw = conn.execute("SELECT body FROM ideas WHERE idea_id = ?", (child["idea_id"],)).fetchone()[0]
        body = json.loads(raw)
        body["planning"] = {"owner": "", "team": "", "budget": None, "target_quarter": "2027-Q1",
                            "depends_on": ["idea_" + "f" * 16]}
        conn.execute("UPDATE ideas SET body = ? WHERE idea_id = ?", (json.dumps(body), child["idea_id"]))
    result = review.review()
    assert any(r["kind"] == "deleted_or_unknown_dependency" for r in result["risk_candidates"])
    assert result["review_authorizes_implementation"] is False


def test_corrupted_legacy_cycle_fails_closed_and_truncates(monkeypatch):
    a = create("A")
    b = create("B", "2027-Q1", [a["idea_id"]])
    with closing(pipeline._connect()) as conn, conn:
        raw = conn.execute("SELECT body FROM ideas WHERE idea_id = ?", (a["idea_id"],)).fetchone()[0]
        body = json.loads(raw)
        body["planning"] = {"owner": "", "team": "", "budget": None, "target_quarter": "2027-Q2",
                            "depends_on": [b["idea_id"]]}
        conn.execute("UPDATE ideas SET body = ? WHERE idea_id = ?", (json.dumps(body), a["idea_id"]))
    result = review.review()
    assert any(r["kind"] == "dependency_cycle" for r in result["risk_candidates"])
    monkeypatch.setattr(review, "MAX_RISKS", 1)
    limited = review.review()
    assert len(limited["risk_candidates"]) == 1
    assert limited["risk_candidates_truncated"] is True
    assert limited["all_ideas_evaluated"] is False


def test_real_http_route_requires_dashboard_session(tmp_path):
    code = r"""
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
path='/v1/automation-pipeline/dependency-schedule'
with TestClient(app) as client:
    assert client.get(path).status_code==401
    token=local_auth.ensure_api_token()
    assert client.get(path,headers={'Authorization':'Bearer '+token}).status_code in (401,403)
    session=local_auth.create_dashboard_session()
    r=client.get(path,headers={'Authorization':'OWG-Session '+session})
    assert r.status_code==200,r.text
    assert r.headers['cache-control']=='no-store'
    data=r.json()
    assert data['format']=='openworkgraph.automation-dependency-schedule-review.v1'
    assert data['schedule_feasibility_verified'] is False
    assert data['risk_candidates']==[]
"""
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"),
                "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    result = subprocess.run([sys.executable, "-c", code], env=env,
                            cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
