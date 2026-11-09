"""Realized value for implemented automation ideas (R16)."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from server import automation_benefits as benefits
from server import automation_pipeline as pipeline

ALL_FIVE = {q: 5 for q in pipeline.EASE_QUESTIONS}
TODAY = date.today()


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "DATA_DIR", tmp_path)


def _idea(implemented=True):
    idea = pipeline.create({"title": "Match remittances"})
    idea = pipeline.transition(idea["idea_id"], {"to": "assessing", "expected_revision": idea["revision"]})
    # Modeled: 300 cases x 10 min x 0.5 = 25 h/month.
    idea = pipeline.assess(idea["idea_id"], {"ease": ALL_FIVE, "cases_per_month": 300, "minutes_per_case": 10,
                                             "eligible_share": 0.5, "expected_revision": idea["revision"]})
    idea = pipeline.transition(idea["idea_id"], {"to": "approved", "expected_revision": idea["revision"]})
    if implemented:
        idea = pipeline.transition(idea["idea_id"], {"to": "implemented", "expected_revision": idea["revision"]})
    return idea


def _period(kind, start, days, **values):
    return {"period": kind, "start": start.isoformat(), "end": (start + timedelta(days=days - 1)).isoformat(), **values}


def test_baseline_and_after_compare_effort_and_volume_against_the_model():
    idea = _idea(implemented=False)
    base_start = TODAY - timedelta(days=90)
    benefits.record(idea["idea_id"], _period("baseline", base_start, 30, cases=300, minutes_per_case=10))
    with pytest.raises(benefits.BenefitError, match="implemented idea"):
        benefits.record(idea["idea_id"], _period("after", TODAY, 1, cases=10, minutes_per_case=2))
    idea = pipeline.transition(idea["idea_id"], {"to": "implemented", "expected_revision": idea["revision"]})
    with pytest.raises(benefits.BenefitError, match="baseline period must end before"):
        benefits.record(idea["idea_id"], _period("baseline", TODAY, 1, cases=1, minutes_per_case=1))
    with pytest.raises(benefits.BenefitError, match="on or after the implementation date"):
        benefits.record(idea["idea_id"], _period("after", TODAY - timedelta(days=5), 3, cases=1, minutes_per_case=1))
    # A one-day after period with more volume and lower per-case effort.
    r = benefits.record(idea["idea_id"], _period("after", TODAY, 1, cases=12, total_hours=0.8))
    assert r["implemented_on"] == TODAY.isoformat()
    after = r["after"]
    assert after["minutes_per_case"] == 4.0 and after["cases_per_month_mean"] == round(12 * 30.44, 2)
    c = r["comparison"]
    assert c["minutes_per_case_change"] == 6.0
    # Per-case saving at the after volume: 6 min x 365.28 cases / 60.
    assert c["hours_per_month_from_per_case_change_at_after_volume"] == round(6 * 12 * 30.44 / 60, 3)
    assert c["modeled_hours_per_month"] == 25.0
    assert c["observed_vs_modeled_ratio"] == round(c["hours_per_month_from_per_case_change_at_after_volume"] / 25.0, 3)
    # Baseline 300 cases x 10 min in 30 days; after 0.8 h on one day, with more cases per month.
    assert c["observed_hours_per_month_change"] == round(50 * 30.44 / 30 - 0.8 * 30.44, 3)
    assert c["case_volume_change_per_month"] == round(12 * 30.44 - 300 * 30.44 / 30, 2) > 0
    assert c["single_period_each"] is True
    assert r["causal_effect_claimed"] is False and r["money_estimated"] is False


def test_validation_limits_and_deletion():
    idea = _idea()
    bad = [_period("baseline", TODAY - timedelta(days=40), 10, cases=1),
           _period("baseline", TODAY - timedelta(days=40), 10, cases=1, minutes_per_case=1, total_hours=1),
           {**_period("baseline", TODAY - timedelta(days=40), 10, cases=1, minutes_per_case=1), "end": (TODAY - timedelta(days=50)).isoformat()},
           _period("baseline", TODAY - timedelta(days=800), 400, cases=1, minutes_per_case=1),
           _period("after", TODAY + timedelta(days=1), 1, cases=1, minutes_per_case=1),
           {**_period("baseline", TODAY - timedelta(days=40), 10, cases=1, minutes_per_case=1), "extra": 1}]
    for payload in bad:
        with pytest.raises(benefits.BenefitError):
            benefits.record(idea["idea_id"], payload)
    with pytest.raises(benefits.BenefitError, match="unknown idea"):
        benefits.report("idea_" + "0" * 16)
    r = benefits.record(idea["idea_id"], _period("baseline", TODAY - timedelta(days=40), 10, cases=100, minutes_per_case=6))
    r = benefits.record(idea["idea_id"], _period("baseline", TODAY - timedelta(days=70), 10, cases=120, minutes_per_case=6))
    assert r["baseline"]["periods"] == 2 and r["comparison"] is None
    assert r["baseline"]["hours_per_month_range"] == [round(100 * 6 / 60 * 3.044, 3), round(120 * 6 / 60 * 3.044, 3)]
    first = r["measurements"][0]["measurement_id"]
    r = benefits.delete(idea["idea_id"], first)
    assert len(r["measurements"]) == 1
    with pytest.raises(benefits.BenefitError, match="unknown measurement"):
        benefits.delete(idea["idea_id"], first)
    pipeline.delete(idea["idea_id"])
    again = _idea()
    assert benefits.report(again["idea_id"])["measurements"] == []


def test_routes_require_the_dashboard(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    code = r'''
from datetime import date, timedelta
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
with TestClient(app) as client:
    owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    idea = client.post("/v1/automation-pipeline", json={"title": "Remittances"}, headers=owner).json()
    path = f"/v1/automation-pipeline/{idea['idea_id']}"
    start = (date.today() - timedelta(days=20)).isoformat(); end = (date.today() - timedelta(days=11)).isoformat()
    body = {"period": "baseline", "start": start, "end": end, "cases": 10, "minutes_per_case": 3}
    assert client.post(path + "/measurements", json=body, headers=bearer).status_code == 403
    r = client.post(path + "/measurements", json=body, headers=owner)
    assert r.status_code == 200, r.text
    mid = r.json()["measurements"][0]["measurement_id"]
    assert client.post(path + "/measurements", json={**body, "period": "after"}, headers=owner).status_code == 422
    assert client.get(path + "/realized-value", headers=bearer).status_code == 403
    assert client.get(path + "/realized-value", headers=owner).json()["baseline"]["periods"] == 1
    assert client.post(path + f"/measurements/{mid}/delete", headers=owner).status_code == 200
    assert client.post(path + f"/measurements/{mid}/delete", headers=owner).status_code == 404
    assert client.get("/v1/automation-pipeline/idea_0000000000000000/realized-value", headers=owner).status_code == 404
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    result = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1], env=env,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
