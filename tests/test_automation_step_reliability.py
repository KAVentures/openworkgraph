"""Version-scoped step reliability regression cases."""
import pytest
from test_automation_run_evidence import store,run,report,imported
from server import automation_monitoring as m
from server import automation_run_evidence as evidence
from server import automation_step_reliability as reliability


def args(digest="a"*64,**changes):
    return {"reported_spec_sha256":digest,"start_date":"2026-01-01","end_date":"2026-01-02",**changes}


def test_run_and_occurrence_denominators():
    d=imported([run("a",steps=[("x","X","failed"),("x","Renamed X","completed"),("y","Y","handed_to_human")]),
                run("b","succeeded",[("x","X","completed")]),run("c","running",[])],"a"*64)
    r=reliability.review(d,args())
    assert r["matching_runs"]==3 and r["matching_steps"]==4
    assert r["runs_without_reported_steps"]==1
    assert r["runs_with_reported_issue_step"]==1
    x,y=r["page"]["rows"]
    assert x["step_id"]=="x" and x["runs_with_step"]==2 and x["occurrences"]==3
    assert x["distinct_reported_activity_labels"]==2
    assert x["runs_with_status"]=={"completed":2,"failed":1,"skipped":0,"handed_to_human":0}
    assert x["runs_with_mixed_statuses"]==x["runs_with_failed_and_completed_occurrences"]==1
    assert x["reported_issue_share_among_runs_with_step"]==.5
    assert x["reported_issue_share_among_all_selected_runs"]==pytest.approx(1/3)
    assert x["runs_where_first_reported_issue_is_step"]==1
    assert y["runs_where_first_reported_issue_is_step"]==0
    p=reliability.review(d,args(step_id="x",expected_population_sha256=r["population_sha256"],limit=1))
    assert p["page"]["matching"]==2 and p["page"]["next_offset"]==1
    row=p["page"]["rows"][0]
    assert row["reference"]["run_id"]=="a" and len(row["matching_step_occurrences"])==2
    assert evidence.detail(d,"a",row["reference"]["run_sha256"])["reference"]==row["reference"]
    assert not r["execution_authorized"]


def test_unknown_version_and_population_drift():
    d=imported([run("a")],"a"*64)
    m.import_report(report([run("unknown")]),d)
    first=reliability.review(d,args())
    unknown=reliability.review(d,args(None))
    assert first["matching_runs"]==unknown["matching_runs"]==1
    assert unknown["unknown_attribution_selected"]
    m.import_report(report([run("a",steps=[("changed","Changed","failed")])],"a"*64),d)
    with pytest.raises(m.MonitoringError,match="changed"):
        reliability.review(d,args(expected_population_sha256=first["population_sha256"]))


@pytest.mark.parametrize("patch",[{"reported_spec_sha256":"bad"},{"start_date":"2026-1-1"},
    {"end_date":"2028-01-01"},{"offset":1},{"step_id":"x"},{"limit":True},{"extra":True}])
def test_invalid_settings(patch):
    d=imported([run("a")])
    with pytest.raises(m.MonitoringError):
        reliability.review(d,{**args(None),**patch})


def test_empty_and_budget(monkeypatch):
    d=imported([run("a")])
    empty=reliability.review(d,args("b"*64))
    assert empty["matching_runs"]==empty["distinct_step_ids"]==0
    monkeypatch.setattr(reliability,"MAX_STEP_IDS",1)
    with pytest.raises(m.MonitoringError,match="distinct"):
        reliability.review(d,args(None))


def test_step_run_filters_preserve_exact_population_and_page_counts():
    d=imported([
        run("a",steps=[("x","X","failed"),("x","X","completed")]),
        run("b",steps=[("y","Y","failed"),("x","X","handed_to_human")]),
        run("c","succeeded",[("x","X","completed")]),
        run("d",steps=[("x","X","failed")])],"a"*64)
    r=reliability.review(d,args())
    pin=r["population_sha256"]
    base=args(step_id="x",expected_population_sha256=pin)
    expected={"all":["a","b","c","d"],"issue":["a","b","d"],"failed":["a","d"],
              "handoff":["b"],"first_issue":["a","d"],"mixed_status":["a"]}
    for kind,ids in expected.items():
        result=reliability.review(d,{**base,"run_filter":kind,"limit":1})
        seen=[]
        while True:
            assert result["population_sha256"]==pin
            assert result["selected_step"]["runs_with_step"]==4
            page=result["page"]
            assert page["run_filter"]==kind and page["matching"]==len(ids)
            seen.extend(x["reference"]["run_id"] for x in page["rows"])
            if page["next_offset"] is None:break
            result=reliability.review(d,{**base,"run_filter":kind,"limit":1,"offset":page["next_offset"]})
        assert seen==ids
    with pytest.raises(m.MonitoringError,match="select a reviewed step"):
        reliability.review(d,args(run_filter="issue"))
    with pytest.raises(m.MonitoringError):
        reliability.review(d,{**base,"run_filter":"arbitrary"})


def test_step_filter_empty_subset_is_not_missing_step():
    d=imported([run("a","succeeded",[("x","X","completed")])],"a"*64)
    r=reliability.review(d,args())
    page=reliability.review(d,args(step_id="x",run_filter="failed",
                          expected_population_sha256=r["population_sha256"]))
    assert page["selected_step"]["runs_with_step"]==1
    assert page["page"]["matching"]==0 and page["page"]["rows"]==[]
    assert page["page"]["next_offset"] is None
