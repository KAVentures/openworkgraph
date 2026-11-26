from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from server import event_log_petri_acceptance as acceptance
from server import event_log_petri_release as release
from server import event_log_petri_soundness as soundness
from server import event_log_pnml as pnml
from server.event_log_workspace import EventLogWorkspaceError


def model(*labels):
    children = [{"op": "activity", "activity": label} for label in labels]
    tree = children[0] if len(children) == 1 else {"op": "seq", "children": children}
    return pnml.to_pnml(pnml.tree_to_net(tree), "reference").encode()


def proposed(trace=("A", "B"), rules=None, **criteria):
    return {"format": release.SPEC_FORMAT,
            "suite": {"format": acceptance.SUITE_FORMAT, "cases": [
                {"id": "expected", "trace": list(trace), "expect": {"completed": True}}]},
            "rules": rules or [], "criteria": criteria}


def assess(a, b, proposal):
    pin = release.pin(a, b, proposal)
    result = release.review(a, b, pin["spec"])
    assert pin["spec_sha256"] == result["spec_sha256"]
    return result


def gates(result):
    return {g["id"]: g["status"] for g in result["gates"]}


def test_same_model_meets_pinned_criteria_and_report_is_reproducible():
    a = model("A", "B")
    proposal = proposed(rules=[{"template": "precedence", "a": "A", "b": "B"}],
                        require_candidate_termination=True, require_candidate_one_safe=True,
                        mutation={"min_detected_basis_points": 10000, "min_mutants": 4,
                                  "settings": {"max_mutants": 4}})
    result = assess(a, a, proposal)
    assert result["status"] == "model_criteria_met"
    assert result["counts"] == {"passed": 8, "failed": 0, "inconclusive": 0}
    assert result["execution_authorized"] is False
    assert result["real_world_outcomes_verified"] is False
    assert result["report_sha256"] == release._digest({k: v for k, v in result.items() if k != "report_sha256"})
    assert result == assess(a, a, proposal)


def test_behavioral_and_acceptance_regression_cannot_be_overridden():
    result = assess(model("A", "B"), model("A", "C"), proposed())
    assert result["status"] == "blocked"
    assert gates(result)["candidate_acceptance"] == "failed"
    assert gates(result)["completed_language_equivalent"] == "failed"
    assert gates(result)["candidate_soundness"] == "passed"


def test_declared_language_change_does_not_require_equivalence():
    result = assess(model("A"), model("B"), proposed(("B",), language="not_required",
                    require_baseline_acceptance=False))
    assert result["status"] == "model_criteria_met"
    assert result["language"] is None
    assert "baseline_acceptance" not in gates(result)


def test_rule_activation_is_distinct_from_vacuous_satisfaction():
    a = model("A", "B")
    rule = {"template": "response", "a": "Never", "b": "B"}
    result = assess(a, a, proposed(rules=[rule]))
    assert gates(result)["candidate_joint_policy"] == "passed"
    assert gates(result)["candidate_policy_1"] == "failed"
    assert result["status"] == "blocked"
    allowed = assess(a, a, proposed(rules=[rule], require_rule_activation=False))
    assert allowed["status"] == "model_criteria_met"


def test_known_policy_violation_blocks_otherwise_sound_model():
    a = model("A", "B")
    result = assess(a, a, proposed(rules=[{"template": "precedence", "a": "B", "b": "A"}]))
    assert gates(result)["candidate_joint_policy"] == "failed"
    assert gates(result)["candidate_policy_1"] == "failed"


def test_mutation_threshold_insufficient_coverage_not_a_pass():
    a = model("A", "B")
    result = assess(a, a, proposed(mutation={"min_detected_basis_points": 0, "min_mutants": 8,
                        "settings": {"max_mutants": 8, "operators": ["hide_activity"]}}))
    assert gates(result)["baseline_suite_mutation_adequacy"] == "inconclusive"
    assert result["status"] == "inconclusive"


@pytest.mark.parametrize("counts,minimum,threshold,expected", [
    ({"detected": 2, "survived": 2, "inconclusive": 0}, 4, 5000, True),
    ({"detected": 2, "survived": 2, "inconclusive": 0}, 4, 5001, False),
    ({"detected": 2, "survived": 1, "inconclusive": 1}, 4, 7500, None),
    ({"detected": 2, "survived": 1, "inconclusive": 1}, 4, 7501, False),
    ({"detected": 4, "survived": 0, "inconclusive": 0}, 5, 0, None),
])
def test_mutation_gate_uses_exact_integer_bounds(counts, minimum, threshold, expected):
    assert release._mutation_gate({"counts": counts, "selected_mutants": 4},
        {"min_mutants": minimum, "min_detected_basis_points": threshold}) is expected


def test_incomplete_marking_graph_never_passes_required_gates(monkeypatch):
    a = model("A", "B")
    monkeypatch.setattr(soundness, "MAX_STATES", 1)
    result = assess(a, a, proposed())
    assert result["status"] == "inconclusive"
    assert gates(result)["candidate_soundness"] == "inconclusive"
    assert gates(result)["candidate_acceptance"] == "inconclusive"
    assert gates(result)["completed_language_equivalent"] == "inconclusive"


def test_exact_input_hashes_reject_model_drift_even_if_only_whitespace_changes():
    a, b = model("A"), model("B")
    spec = release.pin(a, b, proposed(("B",)))["spec"]
    with pytest.raises(EventLogWorkspaceError, match="baseline bytes"):
        release.review(a + b" ", b, spec)
    with pytest.raises(EventLogWorkspaceError, match="candidate bytes"):
        release.review(a, b + b" ", spec)


@pytest.mark.parametrize("criteria", [
    {"language": "anything"}, {"require_baseline_acceptance": 1},
    {"require_candidate_soundness": False}, {"require_candidate_termination": "false"},
    {"mutation": {"min_detected_basis_points": 10001, "min_mutants": 1, "settings": {}}},
    {"mutation": {"min_detected_basis_points": 1, "min_mutants": 2, "settings": {"max_mutants": 1}}},
    {"require_baseline_acceptance": False, "mutation": {
        "min_detected_basis_points": 0, "min_mutants": 1, "settings": {}}},
])
def test_invalid_criteria_refused(criteria):
    a = model("A")
    with pytest.raises(EventLogWorkspaceError):
        release.pin(a, a, proposed(("A",), **criteria))


def test_offline_cli_pin_and_review(tmp_path):
    a = tmp_path / "a.pnml"
    b = tmp_path / "b.pnml"
    spec = tmp_path / "spec.json"
    a.write_bytes(model("A", "B"))
    b.write_bytes(a.read_bytes())
    spec.write_text(json.dumps(proposed()), encoding="utf-8")
    cmd = [sys.executable, "-m", "server.event_log_petri_release"]
    args = ["--baseline", str(a), "--candidate", str(b), "--spec", str(spec)]
    done = subprocess.run(cmd + ["pin"] + args, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    spec.write_text(json.dumps(json.loads(done.stdout)["spec"]), encoding="utf-8")
    done = subprocess.run(cmd + ["review"] + args, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["status"] == "model_criteria_met"
    b.write_bytes(model("B"))
    done = subprocess.run(cmd + ["review"] + args, capture_output=True, text=True, timeout=60)
    assert done.returncode == 2
    assert not done.stdout


def test_owner_only_pin_review_strict_framing_and_no_store(tmp_path):
    code = r'''
import json
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth, event_log_pnml as pnml
data = pnml.to_pnml(pnml.tree_to_net({"op":"activity","activity":"A"}), "reference").encode()
spec = {"format":"openworkgraph.petri_release_spec.v1","suite":{
    "format":"openworkgraph.petri_acceptance_suite.v1","cases":[
    {"id":"required","trace":["A"],"expect":{"completed":True}}]},"rules":[],"criteria":{}}
def frame(raw,b=data):
    return len(raw).to_bytes(4,"big")+raw+len(data).to_bytes(4,"big")+data+b
raw=json.dumps(spec).encode()
path="/v1/event-logs/petri-release/"
with TestClient(app) as client:
    owner={"Authorization":"OWG-Session "+local_auth.create_dashboard_session(),"Content-Type":"application/octet-stream"}
    bearer={"Authorization":"Bearer "+local_auth.ensure_api_token(),"Content-Type":"application/octet-stream"}
    assert client.post(path+"pin",content=frame(raw),headers=bearer).status_code==403
    response=client.post(path+"pin",content=frame(raw),headers=owner)
    assert response.status_code==200,response.text
    assert response.headers["cache-control"]=="no-store"
    pinned=json.dumps(response.json()["spec"]).encode()
    response=client.post(path+"review",content=frame(pinned),headers=owner)
    assert response.status_code==200,response.text
    assert response.json()["status"]=="model_criteria_met"
    assert response.headers["cache-control"]=="no-store"
    assert client.post(path+"review",content=frame(pinned,data+b" "),headers=owner).status_code==422
    assert client.post(path+"pin",content=frame(b'{"format":"a","format":"b"}'),headers=owner).status_code==422
    assert client.post(path+"pin",content=b"x",headers=owner).status_code==422
    assert client.post(path+"pin",content=frame(raw,b""),headers=owner).status_code==422
    assert client.post(path+"pin",content=frame(raw),headers={**owner,"Content-Type":"text/plain"}).status_code==415
print("ok")
'''
    env = {**os.environ, "WORKFLOW_OBSERVER_DATA": str(tmp_path / "srv"),
           "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
           "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")}
    done = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
                          env=env, capture_output=True, text=True, timeout=180)
    assert done.returncode == 0 and done.stdout.strip().endswith("ok"), done.stderr + done.stdout
