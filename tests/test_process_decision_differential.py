"""Differential finite-domain checks: exact paired points, dates and witnesses."""
from copy import deepcopy
from itertools import combinations
import json
import random

import pytest

from shared import process_decision_differential as d
from test_process_decision_domain import model


def packet(m,revision):
    return {"workflow_id":"process1","revision":revision,"content_sha256":str(revision)*64,
            "process_model":m.model_dump()}


def request(values=(-1,0,1,2),dates=("2026-06-01",),**extra):
    return {"dates":list(dates),"dimensions":[{"path":"/amount","choices":[{"value":v} for v in values]}],**extra}


def pair():
    before=model()
    after=model()
    # Before: -1 low, 0 overlap, 1+ high.
    # After: -1 low, 0 low, 1 low, 2 high.
    after.business_rules[0].predicate.conditions[0].expected=1
    after.business_rules[1].predicate.conditions[0].expected=2
    return packet(before,1),packet(after,2)


def test_exact_point_changes_and_exclusive_counts():
    a,b=pair()
    r=d.compare(a,b,request())
    assert r["dated_points"]==4 and r["table_points"]==4
    assert r["changed_points"]==2
    assert r["primary_counts"]=={"selection_gained":1,"target_changed":1,"unchanged":2}
    assert r["nonexclusive_flag_counts"]["diagnostics_changed"]==2
    points=r["evidence_page"]["rows"]
    assert [p["point_index"] for p in points]==[1,2]
    first,second=[p["matching_tables"][0] for p in points]
    assert first["before_status"]=="overlap" and first["after_target"]=="low"
    assert second["before_target"]=="high" and second["after_target"]=="low"
    assert r["witness_suite"]["all_obligations_covered"]
    assert r["witness_suite"]["selected_points"]==2
    assert not r["human_expectations_reviewed"] and not r["execution_authorized"]
    assert r==d.compare(a,b,request())


def test_temporal_expiry_and_table_missing_are_not_ordinary_regressions():
    a,b=pair()
    b["process_model"]["decision_tables"][0]["effective_until"]="2026-07-01"
    r=d.compare(a,b,request(dates=("2026-07-01","2026-06-01")))
    assert r["dates"]==["2026-06-01","2026-07-01"]
    assert r["dated_points"]==8
    assert r["per_date"][0]["changed_points"]==2
    assert r["per_date"][1]["changed_points"]==4
    assert r["nonexclusive_flag_counts"]["selection_lost"]==3
    c=deepcopy(b);c["process_model"]["decision_tables"]=[]
    r=d.compare(a,c,request())
    assert r["primary_counts"]=={"identity_changed":4}
    assert r["nonexclusive_flag_counts"]["selection_lost"]==3
    assert all(x["matching_tables"][0]["after_status"]=="table_missing" for x in r["evidence_page"]["rows"])


def test_first_precedence_diagnostic_change_without_target_change():
    a=model("FIRST");b=model("FIRST")
    b.business_rules[1].predicate.conditions[0].expected=2
    r=d.compare(packet(a,1),packet(b,2),request(values=(0,1,2)))
    assert r["primary_counts"]=={"diagnostics_changed":1,"unchanged":2}
    row=r["evidence_page"]["rows"][0]["matching_tables"][0]
    assert row["before_target"]==row["after_target"]=="low"
    assert row["changed_rows"]==[{"row_id":"row_high","before":"matched","after":"not_matched"}]


def test_downstream_dependencies_evaluated_in_both_revisions():
    a=model(dependent=True);b=model(dependent=True)
    b.business_rules[0].predicate.conditions[0].expected=1
    b.business_rules[1].predicate.conditions[0].expected=1
    r=d.compare(packet(a,1),packet(b,2),request(values=(-1,0,1)))
    assert r["changed_points"]==1
    rows=r["evidence_page"]["rows"][0]["matching_tables"]
    assert {x["table_id"] for x in rows}=={"amount_table","dependent_table"}
    assert all("target_changed" in x["flags"] for x in rows)
    focused=d.compare(packet(a,1),packet(b,2),request(values=(-1,0,1),table_id="dependent_table"))
    assert focused["evidence_page"]["matching_points"]==1


def test_paging_and_fingerprint_and_minimized_report():
    a,b=pair()
    r=d.compare(a,b,request(filter="all",limit=1))
    p=r["evidence_page"];assert p["next_offset"]==1
    s=d.compare(a,b,request(filter="all",limit=1,offset=1,expected_review_sha256=r["review_sha256"]))
    assert s["review_sha256"]==r["review_sha256"]
    assert s["evidence_page"]["rows"][0]["point_index"]==1
    assert d.compare(a,b,request(filter="unchanged"))["evidence_page"]["matching_points"]==2
    with pytest.raises(ValueError,match="first evidence page"):d.compare(a,b,request(offset=1))
    with pytest.raises(ValueError,match="review inputs changed"):
        d.compare(a,b,request(values=(1,2),expected_review_sha256=r["review_sha256"]))
    secret="SYNTHETIC_PRIVATE_VALUE"
    assert secret not in json.dumps(d.compare(a,b,request(values=(secret,))))
    limited=d.compare(a,b,request(witness_limit=1))["witness_suite"]
    assert limited["selected_points"]==1 and not limited["all_obligations_covered"]
    assert limited["uncovered_obligations"]>0 and not limited["optimal_point_count_proven"]


def test_randomized_compact_witnesses_against_exhaustive_set_cover():
    rng=random.Random(9217)
    for _ in range(120):
        n=rng.randint(1,8)
        points=[{"point_index":i,"as_of_date":"2026-01-01","choice_indices":[i],
                 "changed_table_ids":["t"],"obligations":set(rng.sample(range(9),rng.randint(1,6)))}
                for i in range(n)]
        universe=set().union(*(x["obligations"] for x in points))
        optimum=next(k for k in range(1,n+1) if any(set().union(*(x["obligations"] for x in selected))==universe
                            for selected in combinations(points,k)))
        r=d.compact_witnesses(points,n)
        actual=set().union(*(points[x["point_index"]]["obligations"] for x in r["selected"]))
        assert actual==universe and r["covered_obligations"]==len(universe)
        assert r["minimum_possible_point_count_lower_bound"]<=optimum<=r["selected_points"]
        if r["optimal_point_count_proven"]:assert r["selected_points"]==optimum
        assert r==d.compact_witnesses(points,n)
        limited=d.compact_witnesses(points,1)
        assert limited["selected_points"]==1
        assert limited["covered_obligations"]==max(len(x["obligations"]) for x in points)


@pytest.mark.parametrize("change",[
    {"dates":[]},{"dates":["2026-02-30"]},{"dates":["2026-06-01"]*2},
    {"dates":["2026-06-01"]*9},{"witness_limit":True},{"offset":True},{"limit":51},
    {"filter":"invalid"},{"table_id":"absent"},{"expected_review_sha256":"bad"},
    {"dimensions":[{"path":"/amount","choices":[{"value":1},{"value":1.0}]}]},
    {"dimensions":[{"path":"/d"+str(i),"choices":[{"value":v} for v in range(8)]} for i in range(4)]},
    {"execute":True},
])
def test_bad_inputs_refused(change):
    with pytest.raises(ValueError):d.compare(*pair(),request(**change))


def test_guard_released_after_exception(monkeypatch):
    a,b=pair()
    original=d.evaluate_decisions
    def fail(*args):raise ValueError("synthetic failure")
    monkeypatch.setattr(d,"evaluate_decisions",fail)
    with pytest.raises(ValueError,match="synthetic failure"):d.compare(a,b,request())
    monkeypatch.setattr(d,"evaluate_decisions",original)
    assert d.compare(a,b,request())["changed_points"]==2
