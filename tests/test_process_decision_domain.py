"""Finite-domain coverage must agree with the declared evaluator, not invent policy."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from shared.process_model import ProcessModel
from shared.process_decision_domain import analyze, analyze_saved, suggested_domain


def model(policy="UNIQUE", dependent=False):
    activities = [
        {"id": "choose", "label": "Choose", "kind": "decision"},
        {"id": "low", "label": "Low"},
        {"id": "high", "label": "High"},
    ]
    transitions = [
        {"source": "choose", "target": "low", "rule_id": "r_low"},
        {"source": "choose", "target": "high", "rule_id": "r_high"},
    ]
    def rule(id, path, op, expected):
        return {"id": id, "text": "Declared synthetic rule " + id,
                "user_confirmed": True, "predicate": {"conditions": [
                    {"path": path, "operator": op, "expected": expected}]}}
    rules = [rule("r_low", "/amount", "gte" if policy == "FIRST" else "lte", 0),
             rule("r_high", "/amount", "gte", 1 if policy == "FIRST" else 0)]
    def table(id, activity, pairs, policy="UNIQUE", dependencies=None):
        return {"id": id, "decision_activity_id": activity, "hit_policy": policy,
                "effective_from": "2026-01-01", "effective_until": "2027-01-01",
                "source_basis": {"label": "Synthetic policy", "document_sha256": "a"*64},
                "user_confirmed": True, "rows": [
                    {"id": "row_" + target, "rule_id": rule_id, "target_activity_id": target}
                    for rule_id, target in pairs], "required_decisions": dependencies or []}
    tables = [table("amount_table", "choose", [("r_low", "low"), ("r_high", "high")], policy)]
    terminals = ["low", "high"]
    if dependent:
        # Both independent branches continue into the second decision.
        activities.extend([{"id": "next", "label": "Next", "kind": "decision"},
                           {"id": "accept", "label": "Accept"}, {"id": "reject", "label": "Reject"}])
        transitions.extend([{"source": "low", "target": "next"},
                            {"source": "high", "target": "next"},
                            {"source": "next", "target": "accept", "rule_id": "r_accept"},
                            {"source": "next", "target": "reject", "rule_id": "r_reject"}])
        rules[0] = rule("r_low", "/amount", "lt", 0)
        rules[1] = rule("r_high", "/amount", "gte", 0)
        rules.extend([rule("r_accept", "/decisions/amount", "eq", "low"),
                      rule("r_reject", "/decisions/amount", "eq", "high")])
        tables.append(table("dependent_table", "next", [("r_accept", "accept"), ("r_reject", "reject")],
                            dependencies=[{"table_id": "amount_table", "fact_path": "/decisions/amount"}]))
        terminals = ["accept", "reject"]
    return ProcessModel(goal="Check synthetic decision coverage", baseline_source="user_declared",
                        baseline_path=["Choose"], activities=activities, transitions=transitions,
                        business_rules=rules, decision_tables=tables, entry_activity_id="choose",
                        terminal_activity_ids=terminals)


def spec(*values, date="2026-06-01"):
    return {"as_of_date": date, "dimensions": [
        {"path": "/amount", "choices": [{"value": v} if v is not MISSING else {"missing": True}
                                        for v in values]}]}


MISSING = object()


def test_unique_boundary_overlap_and_unknown_are_distinct():
    result = analyze(model(), spec(-1, 0, 1, MISSING, None, True))
    assert result["combinations"] == result["enumerated_combinations"] == 6
    table = result["tables"][0]
    assert table["status_counts"] == {"overlap": 1, "unique_match": 2, "unknown": 3}
    assert table["status_examples"]["overlap"] == [[1]]
    assert table["co_matching_pairs"] == [{"row_ids": ["row_low", "row_high"],
        "both_matched_cases": 1, "witness_choice_indices": [1]}]
    assert [row["selected"] for row in table["rows"]] == [1, 1]
    assert not table["all_cases_selected"]
    assert not result["arbitrary_input_domain_coverage_proven"]
    assert not result["policy_compliance_verified"] and not result["execution_authorized"]


def test_first_shadowing_uses_selected_row_not_just_co_matches():
    result = analyze(model("FIRST"), spec(-1, 0, 1, 2))
    table = result["tables"][0]
    assert table["status_counts"] == {"first_match": 3, "gap": 1}
    first, second = table["rows"]
    assert first["selected"] == 3
    assert second["matched"] == second["shadowed_by_earlier_selection"] == 2
    assert second["selected"] == 0 and second["fully_shadowed_when_matched_in_domain"]
    assert second["examples"]["shadowed"] == [[2], [3]]
    # Adding unknowns must prevent a universal shadowing conclusion.
    second = analyze(model("FIRST"), spec(-1, 0, 1, MISSING))["tables"][0]["rows"][1]
    assert not second["fully_shadowed_when_matched_in_domain"]


def test_dependencies_are_evaluated_in_order_and_conflicts_are_not_overwritten():
    m = model(dependent=True)
    request = spec(-1, 1, MISSING)
    result = analyze(m, request)
    downstream = result["tables"][1]
    assert downstream["status_counts"] == {"dependency_unresolved": 1, "unique_match": 2}
    assert downstream["required_decision_table_ids"] == ["amount_table"]
    assert downstream["undeclared_predicate_paths"] == []
    request["dimensions"].append({"path": "/decisions/amount",
                                  "choices": [{"missing": True}, {"value": "low"}]})
    downstream = analyze(m, request)["tables"][1]
    assert downstream["status_counts"] == {"dependency_fact_conflict": 2, "dependency_unresolved": 2,
                                          "unique_match": 2}


def test_date_and_unconfirmed_rows_do_not_become_unreachable_claims():
    table = analyze(model(), spec(0, date="2027-01-01"))["tables"][0]
    assert table["status_counts"] == {"expired": 1}
    assert all(r["not_evaluated"] == 1 and not r["known_never_matches_in_domain"] for r in table["rows"])
    m = model()
    m.decision_tables[0].user_confirmed = False
    result = analyze(m, spec(0))
    assert result["tables"][0]["status_counts"] == {"unconfirmed": 1}


def test_missing_null_and_bool_number_are_separate_but_duplicate_numbers_refused():
    assert analyze(model(), spec(None, True, 1, MISSING))["combinations"] == 4
    with pytest.raises(ValueError, match="duplicate"):
        analyze(model(), spec(1, 1.0))


@pytest.mark.parametrize("dimensions", [
    [{"path": "/a", "choices": [{"value": 0}]}, {"path": "/a/b", "choices": [{"value": 1}]}],
    [{"path": "/bad~2pointer", "choices": [{"value": 0}]}],
    [{"path": "/a", "choices": [{"missing": 1}]}],
    [{"path": "/a", "choices": [{"value": {"x": 1}}]}],
    [{"path": "/a", "choices": [{"value": float("inf")}]}],
    [{"path": "/a", "choices": [{"value": n} for n in range(25)]}],
    [{"path": "/d"+str(i), "choices": [{"value": n} for n in range(8)]} for i in range(4)],
])
def test_invalid_or_excessive_domains_refused_before_evaluation(dimensions):
    with pytest.raises(ValueError):
        analyze(model(), {"as_of_date": "2026-06-01", "dimensions": dimensions})


def test_suggestions_are_editable_boundary_samples_and_skip_injected_paths():
    m = model(dependent=True)
    suggestion = suggested_domain(m)
    assert len(suggestion["dimensions"]) == 1
    assert suggestion["dimensions"][0]["path"] == "/amount"
    values = [c["value"] for c in suggestion["dimensions"][0]["choices"] if "value" in c]
    assert any(type(x) is float and x < 0 for x in values)
    assert any(type(x) is float and x > 0 for x in values)
    assert suggestion["omitted_paths"] == [{"path": "/decisions/amount",
                                           "reason": "overlapping_or_injected_path"}]
    result = analyze(m, {"as_of_date": "2026-06-01", "dimensions": suggestion["dimensions"]})
    assert result["complete_declared_finite_domain"]


def test_report_is_reproducible_and_does_not_echo_input_values():
    packet = {"workflow_id": "process1", "revision": 2, "content_sha256": "b"*64,
              "process_model": model().model_dump()}
    request = spec("synthetic-secret-value", MISSING, 0)
    report = analyze_saved(packet, request)
    assert report == analyze_saved(packet, request)
    assert report["reference"] == {k: packet[k] for k in ("workflow_id", "revision", "content_sha256")}
    assert "synthetic-secret-value" not in json.dumps(report)
    assert report["domain_sha256"] != analyze_saved(packet, spec("different", MISSING, 0))["domain_sha256"]


def test_real_http_dashboard_scope_and_revision_digest(tmp_path):
    code = r"""
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
from test_process_decision_domain import model, spec
with TestClient(app) as client:
    dashboard = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    saved = client.post("/v1/process-catalog", json={"title":"Coverage", "user_confirmed":True,
        "process_model":model().model_dump()}, headers=dashboard)
    assert saved.status_code == 200, saved.text
    ref = {key:saved.json()[key] for key in ("workflow_id","revision","content_sha256")}
    url = "/v1/process-catalog/decision-domain"
    assert client.get(url+"/suggestions", params=ref, headers=bearer).status_code in (401,403)
    assert client.post(url, params=ref, json=spec(0), headers=bearer).status_code in (401,403)
    suggested = client.get(url+"/suggestions", params=ref, headers=dashboard)
    assert suggested.status_code == 200, suggested.text
    result = client.post(url, params=ref, json=spec(-1,0,1), headers=dashboard)
    assert result.status_code == 200, result.text
    assert result.headers["cache-control"] == "no-store"
    assert result.json()["reference"] == ref
    assert result.json()["tables"][0]["status_counts"] == {"overlap":1,"unique_match":2}
    assert client.post(url, params={**ref,"content_sha256":"0"*64},json=spec(0),headers=dashboard).status_code==409
    assert client.post(url, params={**ref,"revision":99},json=spec(0),headers=dashboard).status_code==404
"""
    env = {**os.environ, "WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"),
           "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
           "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")}
    root = Path(__file__).resolve().parents[1]
    p = subprocess.run([sys.executable, "-c", "import sys;sys.path.insert(0,'tests');" + code],
                       cwd=root, env=env, capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stdout + p.stderr
