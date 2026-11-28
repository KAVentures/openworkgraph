"""R18 exact issue episode and sequence accounting over retained reports."""
from collections import Counter
from copy import deepcopy
from datetime import datetime,timedelta,timezone
import json
import random

import pytest

from test_automation_run_evidence import store,run,report,imported
from server import automation_monitoring as m
from server import automation_run_evidence as e
from server import automation_recovery_patterns as recovery

HASH="a"*64


def settings(**kw):
    return {"reported_spec_sha256":HASH,"start_date":"2026-01-01","end_date":"2026-01-03",**kw}


def test_storage_exact_sequences_and_distinct_run_denominators():
    a=run("a","succeeded",[("x","X","failed"),("y","Y","completed"),("x","X","handed_to_human"),
                          ("x","X","skipped"),("x","X","completed"),("x","X","failed"),("x","X","completed")])
    b=run("b","running",[("x","X","failed"),("y","Y","handed_to_human"),("x","X","failed")])
    c=run("c","failed",[("x","X","completed"),("y","Y","failed")])
    d=run("d","succeeded",[])
    dep=imported([a,b,c,d],HASH)
    r=recovery.review(dep,settings())
    s=r["summary"]
    assert s["runs"]==4 and s["reported_steps"]==12
    assert s["runs_without_reported_steps"]==1
    assert s["run_flag_counts"]=={"has_issue":3,"no_issue":1,"later_completion":1,"unresolved_episode":2,"succeeded_with_issue":1}
    assert s["episode_summary"]["episodes"]==5
    assert s["episode_summary"]["episode_states"]=={"later_completion":2,"running_without_later_completion":2,"terminal_without_later_completion":1}
    assert s["episode_summary"]["failure_occurrences"]==5
    assert s["episode_summary"]["handoff_occurrences"]==2
    assert s["patterns"]==4 and s["distinct_issue_steps"]==2
    assert not r["recovery_verified"] and not r["retry_identity_verified"] and not r["execution_authorized"]
    pin=r["population_sha256"]
    steps=recovery.review(dep,settings(view="steps",expected_population_sha256=pin))["page"]["rows"]
    x=next(x for x in steps if x["step_id"]=="x")
    assert x["runs_with_issue"]==2 and x["episodes"]==3
    assert x["runs_with_later_completion"]==1 and x["runs_with_unresolved_episode"]==1
    patterns=r["page"]["rows"]
    pattern=next(p for p in patterns if p["issue_sequence"]==[["x","failed"],["x","handed_to_human"],["x","failed"]])
    p=recovery.review(dep,settings(view="runs",pattern_sha256=pattern["pattern_sha256"],expected_population_sha256=pin))["page"]
    assert p["matching"]==1
    item=p["rows"][0]
    assert item["reference"]["run_id"]=="a"
    assert e.detail(dep,"a",item["reference"]["run_sha256"])["reference"]==item["reference"]
    assert [row["issue_occurrences"] for row in item["episodes"]]==[2,1]
    assert all(row["state"]=="later_completion" for row in item["episodes"])
    transitions=recovery.review(dep,settings(view="transitions",expected_population_sha256=pin))["page"]["rows"]
    trans=next(t for t in transitions if t["from_issue"]==["x","failed"] and t["to_issue"]==["y","handed_to_human"])
    assert trans["runs"]==trans["occurrences"]==1
    evidence=recovery.review(dep,settings(view="runs",transition_sha256=trans["transition_sha256"],expected_population_sha256=pin))
    assert [x["reference"]["run_id"] for x in evidence["page"]["rows"]]==["b"]


def test_repeated_issue_transition_counts_occurrences_not_runs():
    dep=imported([run("a",steps=[("x","X","failed")]*3),run("b",steps=[("x","X","failed")]*2)],HASH)
    r=recovery.review(dep,settings(view="transitions"))
    assert r["page"]["matching"]==1
    t=r["page"]["rows"][0]
    assert t["runs"]==2 and t["occurrences"]==3
    assert t["share_of_selected_runs"]==1
    assert r["summary"]["episode_summary"]["episodes"]==2


def test_paging_and_exact_stale_evidence_including_label_changes():
    dep=imported([run("a"),run("b"),run("c")],HASH)
    first=recovery.review(dep,settings(view="runs",limit=1))
    pin=first["population_sha256"]
    second=recovery.review(dep,settings(view="runs",limit=1,offset=1,expected_population_sha256=pin))
    assert first["page"]["rows"][0]["reference"]["run_id"]=="a"
    assert second["page"]["rows"][0]["reference"]["run_id"]=="b"
    m.import_report(report([run("outside",day=4)],HASH),dep)
    assert recovery.review(dep,settings())["population_sha256"]==pin
    m.import_report(report([run("a",steps=[("receive","Renamed","completed"),("check","Check","failed")])],HASH),dep)
    with pytest.raises(m.MonitoringError,match="changed"):recovery.review(dep,settings(expected_population_sha256=pin))
    assert recovery.review(dep,settings())["population_sha256"]!=pin


def test_unknown_version_empty_cohort_and_minimized_labels():
    dep=imported([run("a",steps=[("x","PRIVATE_ACTIVITY_LABEL","failed")])],HASH)
    m.import_report(report([run("unknown")]),dep)
    r=recovery.review(dep,settings(view="runs"))
    assert "PRIVATE_ACTIVITY_LABEL" not in json.dumps(r)
    unknown=recovery.review(dep,settings(reported_spec_sha256=None))
    assert unknown["unknown_spec_attribution_selected"] and unknown["summary"]["runs"]==1
    empty=recovery.review(dep,settings(reported_spec_sha256="b"*64))
    assert empty["summary"]["runs"]==empty["summary"]["patterns"]==0
    assert empty["summary"]["episode_summary"]["median_first_issue_to_later_completion_seconds"] is None


def test_filters_are_run_level_and_exact_focus():
    dep=imported([run("a","succeeded",[("x","X","failed"),("x","X","completed"),("y","Y","failed")]),
                  run("b","running",[("y","Y","handed_to_human")]),run("c","succeeded",[])],HASH)
    r=recovery.review(dep,settings())
    pin=r["population_sha256"]
    expected={"all":["a","b","c"],"has_issue":["a","b"],"no_issue":["c"],
              "later_completion":["a"],"unresolved_episode":["a","b"],"succeeded_with_issue":["a"]}
    for kind,ids in expected.items():
        p=recovery.review(dep,settings(view="runs",run_filter=kind,expected_population_sha256=pin))["page"]
        assert [x["reference"]["run_id"] for x in p["rows"]]==ids
    p=recovery.review(dep,settings(view="runs",step_id="y",run_filter="later_completion",expected_population_sha256=pin))["page"]
    # Later completion is explicitly a run-level filter, not necessarily this step.
    assert p["matching"]==1 and p["rows"][0]["reference"]["run_id"]=="a"


def test_elapsed_timestamp_inconsistency_withholds_duration():
    value={"status":"succeeded","steps":[{"seq":1,"step_id":"x","status":"failed","at":"2026-01-01T01:00:00Z"},
                 {"seq":2,"step_id":"x","status":"completed","at":"2026-01-01T00:00:00Z"}]}
    row=recovery.analyze_run(value)["episodes"][0]
    assert row["state"]=="later_completion"
    assert row["timestamp_order_inconsistent"] and row["first_issue_to_completion_seconds"] is None


def test_independent_randomized_episode_partition_oracle():
    rng=random.Random(926)
    for _ in range(160):
        status=rng.choice(["running","succeeded","failed","handed_to_human","cancelled"])
        rows=[{"seq":i,"step_id":rng.choice("abc"),"status":rng.choice(["failed","handed_to_human","completed","skipped"]),
               "at":(datetime(2026,1,1,tzinfo=timezone.utc)+timedelta(seconds=i)).isoformat()}
              for i in range(rng.randrange(35))]
        actual=recovery.analyze_run({"status":status,"steps":rows})
        expected=[]
        # Independent oracle partitions each step's subsequence at each completed
        # occurrence and at the end; issue-bearing intervals are episodes.
        for key in "abc":
            subset=[x for x in rows if x["step_id"]==key]
            issues=[]
            for row in subset+[{"status":"end"}]:
                if row["status"] in {"failed","handed_to_human"}:issues.append(row)
                elif row["status"] in {"completed","end"}:
                    if issues:
                        end=row["seq"] if row["status"]=="completed" else None
                        expected.append((key,issues[0]["seq"],issues[-1]["seq"],len(issues),
                                         sum(x["status"]=="failed" for x in issues),end))
                    issues=[]
        observed=[(x["step_id"],x["first_issue_seq"],x["last_issue_seq"],x["issue_occurrences"],x["failure_occurrences"],x["completion_seq"]) for x in actual["episodes"]]
        assert sorted(observed)==sorted(expected)
        assert sum(x["issue_occurrences"] for x in actual["episodes"])==sum(x["status"] in recovery.ISSUES for x in rows)
        sequence=[(x["step_id"],x["status"]) for x in rows if x["status"] in recovery.ISSUES]
        assert actual["transition_counts"]==Counter(zip(sequence,sequence[1:]))
        for x in actual["episodes"]:
            if x["completion_seq"] is not None:
                assert x["first_issue_to_completion_seconds"]==x["completion_seq"]-x["first_issue_seq"]
            else:
                assert x["state"]==("running_without_later_completion" if status=="running" else "terminal_without_later_completion")


@pytest.mark.parametrize("changes",[{"offset":1},{"limit":True},{"end_date":"2028-01-01"},
    {"start_date":"2026-02-30"},{"reported_spec_sha256":"bad"},{"view":"unknown"},{"extra":True},
    {"step_id":"check"},{"run_filter":"has_issue"},{"view":"runs","pattern_sha256":"b"*64}])
def test_strict_scope_and_paging(changes):
    dep=imported([run("a")],HASH)
    with pytest.raises(m.MonitoringError):recovery.review(dep,settings(**changes))


def test_bounded_scan_refuses_partial_population(monkeypatch):
    dep=imported([run("a"),run("b")],HASH)
    monkeypatch.setattr(recovery,"MAX_RUNS",1)
    with pytest.raises(m.MonitoringError,match="too many retained runs"):recovery.review(dep,settings())
    monkeypatch.setattr(recovery,"MAX_RUNS",100)
    monkeypatch.setattr(recovery,"MAX_STEPS",1)
    with pytest.raises(m.MonitoringError,match="too many retained steps"):recovery.review(dep,settings())
