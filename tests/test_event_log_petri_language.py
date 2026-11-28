from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from server import event_log_petri_language as language
from server import event_log_pnml as pnml
from server import event_log_petri_soundness as soundness


def activity(label):
    return {"op": "activity", "activity": label}


def node(op, *children):
    return {"op": op, "children": list(children)}


def model(tree):
    return pnml.to_pnml(pnml.tree_to_net(tree), "model").encode()


A, B, C = map(activity, "ABC")
TAU = {"op": "tau"}


def test_identical_and_silent_structural_change():
    baseline = model(node("seq", A, B))
    for candidate in (baseline, model(node("seq", TAU, A, TAU, B, TAU))):
        r = language.compare(baseline, candidate)
        assert r["complete"] and r["equivalent"]
        assert r["candidate_preserves_baseline_language"] is True
        assert r["candidate_introduces_no_new_language"] is True
        assert r["added_trace"] is r["removed_trace"] is None


def test_both_directions_and_shortest_witness():
    r = language.compare(model(node("xor", A, node("seq", B, C))),
                         model(node("xor", C, node("seq", A, B))))
    assert r["complete"] and r["equivalent"] is False
    assert r["removed_trace"] == {"activities": ["A"], "length": 1, "truncated": False}
    assert r["added_trace"] == {"activities": ["C"], "length": 1, "truncated": False}


def test_parallel_adds_order_not_removes_original():
    r = language.compare(model(node("seq", A, B)), model(node("and", A, B)))
    assert r["candidate_preserves_baseline_language"] is True
    assert r["candidate_introduces_no_new_language"] is False
    assert r["added_trace"]["activities"] == ["B", "A"]
    assert r["removed_trace"] is None


def test_empty_trace_is_not_missing_witness():
    r = language.compare(model(A), model(node("xor", A, TAU)))
    assert r["added_trace"] == {"activities": [], "length": 0, "truncated": False}
    assert r["candidate_introduces_no_new_language"] is False


def test_nondeterministic_duplicate_labels_union():
    baseline = model(node("xor", node("seq", A, B), node("seq", A, C)))
    candidate = model(node("seq", A, node("xor", B, C)))
    assert language.compare(baseline, candidate)["equivalent"] is True


def test_infinite_trace_language_can_have_finite_equivalence_proof():
    baseline = model(node("loop", A, B))
    candidate = model(node("seq", TAU, node("loop", A, B), TAU))
    assert language.compare(baseline, candidate)["equivalent"] is True
    r = language.compare(baseline, model(A))
    assert r["removed_trace"]["activities"] == ["A", "B", "A"]
    assert r["candidate_introduces_no_new_language"] is True


def test_incomplete_marking_graph_does_not_certify_equivalence(monkeypatch):
    monkeypatch.setattr(soundness, "MAX_STATES", 1)
    r = language.compare(model(A), model(A))
    assert not r["complete"]
    assert r["limit"] == "incomplete_marking_graph"
    assert r["equivalent"] is None
    assert r["removed_trace"] is r["added_trace"] is None


def test_product_limits_do_not_certify_inclusion(monkeypatch):
    monkeypatch.setattr(language, "MAX_PRODUCT_STATES", 1)
    r = language.compare(model(A), model(A))
    assert r["limit"] == "product_state_limit"
    assert r["equivalent"] is None
    monkeypatch.setattr(language, "MAX_PRODUCT_WORK", 0)
    r = language.compare(model(A), model(A))
    assert r["limit"] == "product_work_limit"
    assert r["equivalent"] is None


def test_witness_retained_when_later_product_limit_is_hit(monkeypatch):
    monkeypatch.setattr(language, "MAX_PRODUCT_STATES", 1)
    r = language.compare(model(TAU), model(A))
    assert not r["complete"]
    assert r["equivalent"] is False
    assert r["removed_trace"]["activities"] == []
    assert r["candidate_introduces_no_new_language"] is None


def test_deterministic_report_and_exact_byte_hashes():
    import hashlib
    a, b = model(A), model(B)
    r = language.compare(a, b)
    assert r == language.compare(a, b)
    assert r["baseline"]["pnml_sha256"] == hashlib.sha256(a).hexdigest()
    assert r["candidate"]["pnml_sha256"] == hashlib.sha256(b).hexdigest()


def test_local_endpoint_permissions_framing_and_no_store(tmp_path):
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth, event_log_pnml as pnml
a = pnml.to_pnml(pnml.tree_to_net({"op":"activity","activity":"A"}), "a").encode()
b = pnml.to_pnml(pnml.tree_to_net({"op":"activity","activity":"B"}), "b").encode()
body = len(a).to_bytes(4, "big") + a + b
path = "/v1/event-logs/petri-language-comparison"
with TestClient(app) as client:
    owner = {"Authorization":"OWG-Session " + local_auth.create_dashboard_session(),
             "Content-Type":"application/octet-stream"}
    bearer = {"Authorization":"Bearer " + local_auth.ensure_api_token(),
              "Content-Type":"application/octet-stream"}
    assert client.post(path, content=body, headers=bearer).status_code == 403
    response = client.post(path, content=body, headers=owner)
    assert response.status_code == 200, response.text
    assert response.json()["removed_trace"]["activities"] == ["A"]
    assert response.json()["added_trace"]["activities"] == ["B"]
    assert response.headers["cache-control"] == "no-store"
    assert client.post(path, content=b"x", headers=owner).status_code == 422
    assert client.post(path, content=len(a).to_bytes(4,"big")+a, headers=owner).status_code == 422
    assert client.post(path, content=(2*1024*1024+1).to_bytes(4,"big")+a+b, headers=owner).status_code == 413
    assert client.post(path, content=body, headers={**owner,"Content-Type":"text/plain"}).status_code == 415
print("ok")
'''
    env = {**os.environ, "WORKFLOW_OBSERVER_DATA": str(tmp_path / "srv"),
           "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
           "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")}
    done = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
                          env=env, capture_output=True, text=True, timeout=180)
    assert done.returncode == 0 and done.stdout.strip().endswith("ok"), done.stderr + done.stdout
