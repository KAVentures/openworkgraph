"""Portfolio governance for the automation pipeline (R15): owners, teams, budgets, dependencies."""
from __future__ import annotations

import csv
import io

import pytest

from server import automation_pipeline as pipeline

ALL_FIVE = {q: 5 for q in pipeline.EASE_QUESTIONS}


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "DATA_DIR", tmp_path)


def _plan(idea, **fields):
    return pipeline.plan(idea["idea_id"], {"expected_revision": idea["revision"], **fields})


def _approve(idea):
    idea = pipeline.transition(idea["idea_id"], {"to": "assessing", "expected_revision": idea["revision"]})
    idea = pipeline.assess(idea["idea_id"], {"ease": ALL_FIVE, "cases_per_month": 120, "minutes_per_case": 10,
                                             "eligible_share": 1, "expected_revision": idea["revision"]})
    return pipeline.transition(idea["idea_id"], {"to": "approved", "expected_revision": idea["revision"]})


def test_planning_is_versioned_and_recorded():
    idea = pipeline.create({"title": "Extract invoice totals"})
    planned = _plan(idea, owner=" Finance ops ", team="AP", target_quarter="2027-Q1",
                    budget={"amount": 12000, "currency": "EUR"})
    assert planned["planning"] == {"owner": "Finance ops", "team": "AP", "target_quarter": "2027-Q1",
                                   "budget": {"amount": 12000, "currency": "EUR", "basis": "estimate"}, "depends_on": []}
    assert planned["revision"] == 2 and planned["history"][-1]["action"] == "planning_saved"
    assert planned["history"][-1]["previous_planning"] is None
    with pytest.raises(pipeline.PipelineError, match="changed"):
        _plan(idea, owner="x")
    for bad in ({"target_quarter": "2027-Q5"}, {"budget": {"amount": -1, "currency": "EUR"}},
                {"budget": {"amount": 1, "currency": "eur"}}, {"depends_on": ["not-an-id"]}, {"unknown": 1}):
        with pytest.raises(pipeline.PipelineError):
            _plan(planned, **bad)


def test_dependencies_refuse_self_unknown_and_cycles():
    a, b, c = (pipeline.create({"title": t}) for t in ("A", "B", "C"))
    with pytest.raises(pipeline.PipelineError, match="itself"):
        _plan(a, depends_on=[a["idea_id"]])
    with pytest.raises(pipeline.PipelineError, match="unknown dependency"):
        _plan(a, depends_on=["idea_" + "0" * 16])
    b = _plan(b, depends_on=[a["idea_id"]])
    c = _plan(c, depends_on=[b["idea_id"], b["idea_id"]])
    assert c["planning"]["depends_on"] == [b["idea_id"]]
    with pytest.raises(pipeline.PipelineError, match="cycle"):
        _plan(pipeline.get(a["idea_id"]), depends_on=[c["idea_id"]])
    with pytest.raises(pipeline.PipelineError, match="depend on this one"):
        pipeline.delete(a["idea_id"])


def test_an_idea_is_implemented_only_after_its_dependencies():
    base, top = pipeline.create({"title": "Clean vendor master"}), pipeline.create({"title": "Auto-match invoices"})
    top = _approve(_plan(top, depends_on=[base["idea_id"]]))
    with pytest.raises(pipeline.PipelineError, match=r"implement its dependencies first: Clean vendor master \(idea\)"):
        pipeline.transition(top["idea_id"], {"to": "implemented", "expected_revision": top["revision"]})
    base = _approve(base)
    pipeline.transition(base["idea_id"], {"to": "implemented", "expected_revision": base["revision"]})
    done = pipeline.transition(top["idea_id"], {"to": "implemented", "expected_revision": top["revision"]})
    assert done["state"] == "implemented"


def test_portfolio_rolls_up_per_team_owner_and_currency():
    a = _plan(pipeline.create({"title": "A"}), team="AP", owner="Sam", budget={"amount": 1000, "currency": "EUR"},
              target_quarter="2027-Q1")
    b = _plan(pipeline.create({"title": "B"}), team="AP", owner="Kim", depends_on=[a["idea_id"]],
              budget={"amount": 500, "currency": "USD", "basis": "approved_budget"}, target_quarter="2027-Q2")
    c = _plan(pipeline.create({"title": "C"}), team="AP", budget={"amount": 9999, "currency": "EUR"})
    pipeline.transition(c["idea_id"], {"to": "rejected", "expected_revision": c["revision"]})
    _approve(a)
    pipeline.create({"title": "Unplanned"})
    r = pipeline.portfolio()
    assert r["ideas"] == 4 and r["unplanned_ideas"] == 1 and r["budgets_converted_between_currencies"] is False
    ap = next(t for t in r["by_team"] if t["team"] == "AP")
    assert ap["ideas"] == 3 and ap["states"]["approved"] == 1 and ap["states"]["rejected"] == 1
    assert ap["budgets"] == {"EUR:estimate": 1000, "USD:approved_budget": 500}, "rejected budgets are excluded"
    assert ap["modeled_hours_per_month"] == 20.0
    assert r["by_team"][-1]["team"] is None and r["by_team"][-1]["ideas"] == 1
    assert {o["owner"] for o in r["by_owner"]} == {"Sam", "Kim", None}
    assert r["blocked"] == [{"idea_id": b["idea_id"], "title": "B", "state": "idea",
                             "waiting_on": [{"idea_id": a["idea_id"], "title": "A", "state": "approved"}]}]
    assert r["dependencies"] == [{"idea_id": b["idea_id"], "depends_on": a["idea_id"]}]
    assert r["open_ideas_by_target_quarter"] == {"2027-Q1": 1, "2027-Q2": 1}


def test_csv_includes_planning_with_formula_guard():
    idea = _plan(pipeline.create({"title": "A"}), owner="=HYPERLINK(1)", team="AP",
                 budget={"amount": 10, "currency": "GBP"})
    rows = list(csv.DictReader(io.StringIO(pipeline.export_csv())))
    assert rows[0]["owner"] == "'=HYPERLINK(1)" and rows[0]["budget_currency"] == "GBP"
    assert rows[0]["budget_amount"] == "10" and rows[0]["idea_id"] == idea["idea_id"]


def test_planning_and_portfolio_routes_require_the_dashboard(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
with TestClient(app) as client:
    owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    a = client.post("/v1/automation-pipeline", json={"title": "Base"}, headers=owner).json()
    b = client.post("/v1/automation-pipeline", json={"title": "Top"}, headers=owner).json()
    body = {"team": "AP", "depends_on": [a["idea_id"]], "budget": {"amount": 5, "currency": "EUR"}, "expected_revision": 1}
    assert client.post(f"/v1/automation-pipeline/{b['idea_id']}/planning", json=body, headers=bearer).status_code == 403
    r = client.post(f"/v1/automation-pipeline/{b['idea_id']}/planning", json=body, headers=owner)
    assert r.status_code == 200, r.text
    stale = client.post(f"/v1/automation-pipeline/{b['idea_id']}/planning", json=body, headers=owner)
    assert stale.status_code == 409, stale.text
    cycle = client.post(f"/v1/automation-pipeline/{a['idea_id']}/planning",
                        json={"depends_on": [b["idea_id"]], "expected_revision": 1}, headers=owner)
    assert cycle.status_code == 422 and "cycle" in cycle.json()["detail"]
    assert client.get("/v1/automation-pipeline/portfolio", headers=bearer).status_code == 403
    p = client.get("/v1/automation-pipeline/portfolio", headers=owner)
    assert p.status_code == 200, p.text
    assert p.json()["blocked"][0]["idea_id"] == b["idea_id"]
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    result = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1], env=env,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
