"""R16: review potential duplicate benefit evidence across local implemented ideas."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import automation_benefit_overlap as overlap
from server import automation_benefits as benefits
from server import automation_pipeline as pipeline

TODAY = datetime.now(timezone.utc).date().isoformat()
ALL_FIVE = {question: 5 for question in pipeline.EASE_QUESTIONS}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "DATA_DIR", tmp_path)


def idea(title: str) -> str:
    row = pipeline.create({"title": title})
    row = pipeline.transition(row["idea_id"], {"to": "assessing", "expected_revision": row["revision"]})
    row = pipeline.assess(row["idea_id"], {"ease": ALL_FIVE, "cases_per_month": 10,
        "minutes_per_case": 30, "eligible_share": 1, "expected_revision": row["revision"]})
    row = pipeline.transition(row["idea_id"], {"to": "approved", "expected_revision": row["revision"]})
    row = pipeline.transition(row["idea_id"], {"to": "implemented", "expected_revision": row["revision"]})
    return row["idea_id"]


def measurement(idea_id: str, source: str = "event_log", reference: str = "log-2026"):
    benefits.record(idea_id, {"period": "after", "start": TODAY, "end": TODAY, "cases": 20,
         "minutes_per_case": 10, "source": source, "source_reference": reference})


def test_empty_store_has_no_provider_access_or_claims():
    r = overlap.review()
    assert r["candidate_pairs"] == []
    assert r["execution_authorized"] is False
    assert r["realized_savings_verified"] is False
    assert r["source_identity_verified"] is False
    assert r["source_reference_values_returned"] is False


def test_cross_idea_overlap_uses_exact_declared_source_and_inclusive_dates():
    a, b, c, d = (idea(name) for name in ("Automate A", "Automate B", "Automate C", "Automate D"))
    measurement(a)
    measurement(b)
    measurement(c, source="time_tracking", reference="log-2026")
    measurement(d, reference="")
    r = overlap.review()
    assert r["status"] == "review_candidates"
    assert r["after_measurements"] == 4
    assert r["unlinked_after_measurements"] == 1
    assert len(r["candidate_pairs"]) == 1
    pair = r["candidate_pairs"][0]
    assert {pair["first_idea_id"], pair["second_idea_id"]} == {a, b}
    assert pair["overlap_start"] == TODAY and pair["overlap_end"] == TODAY
    assert pair["same_cases_verified"] is False
    assert pair["source_kind"] == "event_log"
    assert "log-2026" not in json.dumps(r)  # No raw provider source references.
    assert r["comparison_complete"] is True
    assert r["realized_savings_verified"] is False


def test_same_idea_is_not_double_counted_as_cross_idea_conflict():
    a = idea("One idea")
    measurement(a)
    measurement(a)
    r = overlap.review()
    assert r["candidate_pairs"] == [] and r["after_measurements"] == 2
    assert r["status"] == "no_exact_overlap_detected"


def test_malformed_stored_evidence_is_not_silently_counted_clear():
    a, b = idea("A"), idea("B")
    measurement(a)
    measurement(b)
    with closing(pipeline._connect()) as conn, conn:
        conn.execute("UPDATE idea_measurements SET body = ? WHERE idea_id = ?", ('not-json', b))
    r = overlap.review()
    assert r["status"] == "incomplete_evidence"
    assert r["unreadable_measurements"] == 1
    assert r["comparison_complete"] is False


def test_pure_overlap_scan_handles_time_boundaries_and_truncation(monkeypatch):
    from datetime import date
    def row(idea_id, mid, lo, hi):
        return {"idea_id": idea_id, "measurement_id": mid, "start": date.fromisoformat(lo),
                "end": date.fromisoformat(hi), "source_key": ("event_log", "x")}
    rows = [
        row("a","1","2026-01-01","2026-01-02"),
        row("b","2","2026-01-02","2026-01-03"),
        row("c","3","2026-01-04","2026-01-05"),
    ]
    pairs, truncated = overlap._candidates(rows)
    assert len(pairs) == 1 and not truncated
    assert pairs[0]["overlap_start"] == "2026-01-02"
    monkeypatch.setattr(overlap, "MAX_CANDIDATES", 1)
    rows += [row("d","4","2026-01-02","2026-01-03")]
    pairs, truncated = overlap._candidates(rows)
    assert len(pairs) == 1 and truncated


def test_real_http_route_requires_local_dashboard_session(tmp_path):
    code = r"""
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
path='/v1/automation-pipeline/benefit-overlap'
with TestClient(app) as client:
    assert client.get(path).status_code == 403
    token = local_auth.ensure_api_token()
    assert client.get(path,headers={'Authorization':'Bearer '+token}).status_code in (401,403)
    session = local_auth.create_dashboard_session()
    response = client.get(path,headers={'Authorization':'OWG-Session '+session})
    assert response.status_code == 200,response.text
    assert response.headers['cache-control'] == 'no-store'
    body=response.json()
    assert body['format']=='openworkgraph.automation-benefit-overlap-review.v1'
    assert body['execution_authorized'] is False
    assert body['candidate_pairs']==[]
"""
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"),
                "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    run = subprocess.run([sys.executable, "-c", code],
                         cwd=Path(__file__).resolve().parents[1], env=env,
                         capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, f"stdout={run.stdout}\nstderr={run.stderr}"
