from collections import Counter
from types import SimpleNamespace

from server import event_log_petri_trace_audit as audit
from server import event_log_pnml as pnml
from server import event_log_petri_replay as replay


def make(tree):
    return pnml.to_pnml(pnml.tree_to_net(tree), "test").encode()


def act(a):
    return {"op": "activity", "activity": a}


def seq(*children):
    return {"op": "seq", "children": list(children)}


def check(data, trace):
    return audit.check(replay.parse_pnml(data), trace, [0])


def test_duplicate_label_greedy_false_negative_is_accepted_by_exact_audit():
    data = make({"op": "xor", "children": [seq(act("A"), act("B")), seq(act("A"), act("C"))]})
    net = replay.parse_pnml(data)
    by_label = {}
    for t, label in sorted(net["transitions"].items()):
        if label is not None:
            by_label.setdefault(label, []).append(t)
    assert not replay._replay(net, ("A", "C"), by_label)["fits"]
    result = check(data, ["A", "C"])
    assert result["status"] == "accepted"
    ids = result["witness"]["transition_ids"]
    assert [net["transitions"][t] for t in ids if net["transitions"][t] is not None] == ["A", "C"]
    marking = net["initial"].copy()
    for t in ids:
        assert replay._enabled(marking, net["pre"][t])
        marking.subtract(net["pre"][t])
        marking.update(net["post"][t])
    assert +marking == net["final"]


def test_rejection_and_prefix():
    data = make(seq(act("A"), act("B")))
    assert check(data, ["A", "B"])["status"] == "accepted"
    r = check(data, ["A", "C"])
    assert r["status"] == "rejected" and r["longest_prefix"] == 1
    assert check(data, ["A"])["status"] == "rejected"


def test_silent_cycle_terminates_exploration():
    net = {"initial": Counter(p=1), "final": Counter(q=1),
           "transitions": {"tau": None, "a": "A"},
           "pre": {"tau": Counter(p=1), "a": Counter(p=1)},
           "post": {"tau": Counter(p=1), "a": Counter(q=1)}}
    assert audit.check(net, ["A"], [0])["status"] == "accepted"
    assert audit.check(net, ["B"], [0])["status"] == "rejected"


def test_limits_do_not_prove_rejection(monkeypatch):
    data = make(seq(act("A"), act("B")))
    monkeypatch.setattr(audit, "MAX_STATES", 1)
    assert check(data, ["A", "B"])["status"] == "inconclusive"
    monkeypatch.setattr(audit, "MAX_WORK", 0)
    assert check(data, ["A", "B"])["limit"] == "work_limit"


def test_population_bounds_include_excluded_and_inconclusive(monkeypatch):
    data = make(seq(act("A"), act("B")))
    monkeypatch.setattr(audit.workspace, "_query_filtered", lambda *a: {
        "rows": [(["A", "B"], 5, "fit"), (["A", "C"], 3, "bad")],
        "total": 10, "case_notion": "case"})
    report = audit.audit("log", data)
    assert report["counts"] == {"accepted": 5, "rejected": 3, "inconclusive": 0}
    assert report["cases_excluded"] == 2
    assert report["accepted_fraction_bounds"] == {"lower": .5, "upper": .7}
    monkeypatch.setattr(audit, "MAX_WORK", 0)
    report = audit.audit("log", data)
    assert report["counts"]["inconclusive"] == 8
    assert report["accepted_fraction_bounds"] == {"lower": 0, "upper": 1}
