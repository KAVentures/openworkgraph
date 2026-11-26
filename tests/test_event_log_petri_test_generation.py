import pytest
from server import event_log_petri_test_generation as generation
from server import event_log_petri_acceptance as acceptance
from server import event_log_petri_soundness as soundness
from server import event_log_pnml as pnml
from server.event_log_workspace import EventLogWorkspaceError


def activity(label):
    return {"op": "activity", "activity": label}


def node(op, *children):
    return {"op": op, "children": list(children)}


def model(tree):
    return pnml.to_pnml(pnml.tree_to_net(tree), "reference").encode()


A, B, C = map(activity, "ABC")
TAU = {"op": "tau"}


@pytest.mark.parametrize("tree", [
    node("seq", A, B),
    node("xor", node("seq", A, B), node("seq", A, C)),
    node("and", A, B),
    node("loop", A, B),
    node("xor", TAU, A),
    TAU,
])
def test_generated_expectations_replay_on_reference(tree):
    net = model(tree)
    report = generation.generate(net)
    assert report["status"] == "generated"
    assert report["suite"] is not None
    assert report == generation.generate(net)
    checked = acceptance.review(net, report["suite"])
    assert checked["baseline"]["suite_passed"] is True
    assert checked["suite_sha256"] == report["suite_sha256"]
    assert checked["baseline"]["model"]["pnml_sha256"] == report["model"]["pnml_sha256"]


def test_sequence_contains_positive_negative_and_prefix_cases():
    report = generation.generate(model(node("seq", A, B)))
    assert report["prefix_exploration_complete"]
    assert {"completed_trace", "negative_prefix", "noncompleted_prefix"} <= set(report["categories_selected"])
    assert any(c["trace"] == ["A", "B"] and c["expect"]["completed"] for c in report["suite"]["cases"])
    assert any(c["expect"].get("exact_next") == ["B"] for c in report["suite"]["cases"])


def test_generated_suite_detects_reordered_candidate():
    a, b = model(node("seq", A, B)), model(node("seq", B, A))
    report = generation.generate(a)
    review = acceptance.review(a, report["suite"], b)
    assert review["baseline"]["suite_passed"] is True
    assert review["candidate"]["suite_passed"] is False
    assert review["changes"]["regressions"] > 0


def test_case_limit_and_omitted_categories():
    report = generation.generate(model(node("seq", A, B)), {"max_cases": 1})
    assert len(report["suite"]["cases"]) == 1
    assert report["categories_omitted"]
    assert report["prefix_exploration_complete"]


def test_depth_limit_is_not_an_exact_coverage_claim():
    net = model(node("seq", A, B, C))
    report = generation.generate(net, {"max_depth": 1})
    assert not report["prefix_exploration_complete"]
    assert report["limit"] == "depth_limit"
    assert acceptance.review(net, report["suite"])["baseline"]["suite_passed"] is True


def test_incomplete_marking_graph_refuses_generation(monkeypatch):
    monkeypatch.setattr(soundness, "MAX_STATES", 1)
    report = generation.generate(model(A))
    assert report["suite"] is None
    assert report["limit"] == "incomplete_marking_graph"


def test_work_and_selection_limits_explicit(monkeypatch):
    net = model(A)
    monkeypatch.setattr(generation, "MAX_WORK", 0)
    report = generation.generate(net)
    assert report["limit"] == "work_limit"
    assert report["suite"] is None
    monkeypatch.setattr(generation, "MAX_WORK", 600000)
    monkeypatch.setattr(generation, "MAX_SELECTION_WORK", 0)
    report = generation.generate(net)
    assert report["selection_limited"]
    assert report["suite"] is None


@pytest.mark.parametrize("settings", [{"max_cases": True}, {"max_cases": 41}, {"max_depth": 0}, {"unknown": 1}])
def test_invalid_settings(settings):
    with pytest.raises(EventLogWorkspaceError):
        generation.generate(model(A), settings)
